"""Compile product and unit witnesses from complete source tables, never model claims."""

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from math import isfinite

from duckdb import DuckDBPyConnection

from datamate.preparation.models import (
    Binding,
    EvidenceRef,
    ResolvedBinding,
    SemanticBindings,
    TableProfile,
)
from datamate.preparation.storage import iter_raw_batches
from datamate.preparation.text_state import effective_values

NATIVE_RELATION_VERSION = "source-row-relations-v1"


def has_native_proof(binding: Binding, bindings: SemanticBindings) -> bool:
    """Only locally compiled entries may use row evidence instead of literal clauses."""
    entry = next((e for e in bindings.entries if e.binding == binding), None)
    if (
        entry is None
        or entry.status != "ACCEPTED"
        or entry.acceptance_rule != NATIVE_RELATION_VERSION
    ):
        return False
    profiles = {t: (d, source, digest) for t, d, source, digest in bindings.profile_sources}
    target = profiles.get(binding.table_id)
    if target is None or target[0] != binding.profile_digest:
        return False
    source_hashes = {(source, digest) for _, source, digest in profiles.values()}
    rows = [e for e in binding.evidence if e.locator.startswith("raw:")]
    return bool(rows) and all(
        (e.source, e.sha256) in source_hashes
        and e.source.split("/")[:2] == target[1].split("/")[:2]
        for e in rows
    )


def _roles(bindings, table):
    groups = defaultdict(list)
    for b in bindings.accepted:
        if b.table_id == table and b.kind == "role" and len(b.columns) == 1:
            groups[b.value].append(b)
    return {role: values[0] for role, values in groups.items() if len(values) == 1}


def _rows(db, profile):
    for batch in iter_raw_batches(db, profile.table_id, 1000):
        for record in batch:
            if record.kind == "data":
                yield record.record_id, effective_values(db, record.record_id, record.source.values)


def _value(values, binding):
    return values[int(binding.columns[0][1:]) - 1]


def _key(value):
    return value if type(value) is str and value and value == value.strip() else None


def _factor(value):
    # Grouped/locale-ambiguous strings need a separate source grammar, not guessing.
    if type(value) is float and isfinite(value):
        value = Decimal(str(value))
    elif type(value) is int:
        value = Decimal(value)
    elif type(value) is str and value and all(c in "0123456789." for c in value):
        try:
            value = Decimal(value)
        except InvalidOperation:
            return None
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        return None
    parts = value.as_tuple()
    if max(len(parts.digits) + max(parts.exponent, 0), -min(parts.exponent, 0)) > 38:
        return None
    return value


def _evidence(profile, record, roles):
    return tuple(
        dict.fromkeys(
            [
                *(e for role in roles for e in role.evidence),
                *(
                    EvidenceRef(
                        source=profile.source,
                        sha256=profile.source_sha256,
                        locator=f"raw:{record}/{role.columns[0]}",
                    )
                    for role in roles
                ),
            ]
        )
    )


def enrich_native_relations(
    db: DuckDBPyConnection, profiles: Sequence[TableProfile], bindings: SemanticBindings
) -> SemanticBindings:
    """Derive exact-key, product-scoped relations; duplicate domains stay unresolved."""
    roles = {p.table_id: _roles(bindings, p.table_id) for p in profiles}
    products = defaultdict(list)
    names = defaultdict(list)
    conversions = defaultdict(list)
    issues = []
    blocked = set()
    for profile in profiles:
        r = roles[profile.table_id]
        identity = r.get("product_id") or r.get("product_name")
        if identity is None:
            continue
        if "unit_factor" in r and "unit" in r:
            allowed = {
                "product_id",
                "product_name",
                "unit",
                "unit_factor",
                "base_unit",
                "unit_cost",
            }
            known = {b.columns[0] for role, b in r.items() if role in allowed}
            if any(c.column_id not in known for c in profile.columns):
                issues.append(
                    (profile.table_id, "", "conversion has unsupported scope or extra fields")
                )
                for _, values in _rows(db, profile):
                    key = _key(_value(values, identity))
                    if key is not None:
                        blocked.add((identity.value, key))
                continue
            required = [identity, r["unit"], r["unit_factor"]]
            if "base_unit" in r:
                required.append(r["base_unit"])
            for record, values in _rows(db, profile):
                key = _key(_value(values, identity))
                unit = _key(_value(values, r["unit"]))
                if key is None or unit is None:
                    continue
                base = _key(_value(values, r["base_unit"])) if "base_unit" in r else None
                conversions[(identity.value, key, unit)].append(
                    (
                        _factor(_value(values, r["unit_factor"])),
                        base,
                        _evidence(profile, record, required),
                    )
                )
        elif (
            "product_id" in r
            and "product_name" in r
            and not ({"quantity", "inventory_quantity", "order_id", "payment_amount"} & r.keys())
        ):
            required = [r["product_id"], r["product_name"]]
            if "base_unit" in r:
                required.append(r["base_unit"])
            for record, values in _rows(db, profile):
                code = _key(_value(values, r["product_id"]))
                name = _key(_value(values, r["product_name"]))
                if code is None or name is None:
                    continue
                base = _key(_value(values, r["base_unit"])) if "base_unit" in r else None
                item = (code, name, base, _evidence(profile, record, required))
                products[code].append(item)
                names[name].append(item)

    additions = []
    for profile in profiles:
        r = roles[profile.table_id]
        product = r.get("product_id") or r.get("product_name")
        quantity = r.get("quantity") or r.get("inventory_quantity")
        if product is None or quantity is None or "unit" not in r or "unit_factor" in r:
            continue
        unit_role = r["unit"]
        dependencies = (product.binding_id, quantity.binding_id, unit_role.binding_id)
        target_evidence = tuple(
            dict.fromkeys(e for b in (product, quantity, unit_role) for e in b.evidence)
        )
        by_product = defaultdict(list)
        for (identity_kind, key, unit), matches in conversions.items():
            linked = products.get(key, []) if identity_kind == "product_id" else names.get(key, [])
            if identity_kind == product.value:
                target_key = key
                link = linked[0] if len(linked) == 1 else None
                # A duplicated catalog key is not authorized by a unique conversion row.
                if len(linked) > 1:
                    issues.append(
                        (profile.table_id, key, "ambiguous or duplicate product catalog key")
                    )
                    continue
            elif len(linked) == 1:
                link = linked[0]
                target_key = link[0] if product.value == "product_id" else link[1]
                target_domain = products if product.value == "product_id" else names
                if len(target_domain[target_key]) != 1:
                    issues.append((profile.table_id, target_key, "ambiguous product identity"))
                    continue
            else:
                issues.append((profile.table_id, key, "missing or ambiguous source product join"))
                continue
            if len(matches) != 1 or matches[0][0] is None:
                issues.append(
                    (
                        profile.table_id,
                        target_key,
                        "duplicate, conflicting or invalid conversion rows",
                    )
                )
                continue
            factor, base, evidence = matches[0]
            if link is not None:
                if base is not None and link[2] is not None and base != link[2]:
                    issues.append(
                        (
                            profile.table_id,
                            target_key,
                            "conversion base disagrees with product catalog",
                        )
                    )
                    continue
                base = base or link[2]
                evidence = tuple(dict.fromkeys((*evidence, *link[3])))
            if base is None:
                issues.append((profile.table_id, target_key, "source base unit is not declared"))
                continue
            by_product[target_key].append((unit, base, factor, evidence))
        for key, edges in by_product.items():
            linked = products.get(key, []) if product.value == "product_id" else names.get(key, [])
            identity_keys = {(product.value, key)}
            if len(linked) == 1:
                identity_keys.update({("product_id", linked[0][0]), ("product_name", linked[0][1])})
            if identity_keys & blocked:
                issues.append(
                    (profile.table_id, key, "unsupported conditional source applies to product")
                )
                continue
            bases = {base for _, base, _, _ in edges}
            counts = Counter(unit for unit, _, _, _ in edges)
            if len(bases) != 1 or any(n != 1 for n in counts.values()):
                issues.append((profile.table_id, key, "conflicting source unit domain"))
                continue
            base = next(iter(bases))
            shared = tuple(dict.fromkeys((*target_evidence, *edges[0][3])))
            # Foreign row coordinates stay intact; table_id selects the application scope.
            common = dict(
                kind="unit",
                table_id=profile.table_id,
                columns=unit_role.columns,
                profile_digest=profile.digest,
                dependencies=dependencies,
                unit_product=key,
            )
            declarations = [Binding(**common, value=base, unit_base=True, evidence=shared)]
            for unit, _, factor, evidence in edges:
                if unit == base:
                    if factor != 1:
                        declarations = []
                        issues.append((profile.table_id, key, "base unit has a non-unit factor"))
                        break
                    continue
                declarations.append(
                    Binding(
                        **common,
                        value=unit,
                        unit_base=False,
                        unit_target=base,
                        unit_factor=factor,
                        evidence=tuple(dict.fromkeys((*target_evidence, *evidence))),
                    )
                )
            additions.extend(
                ResolvedBinding(
                    b,
                    "ACCEPTED",
                    "validated native source rows",
                    acceptance_rule=NATIVE_RELATION_VERSION,
                )
                for b in declarations
            )
    db.execute(
        "CREATE TABLE meta.source_relation_issues("
        "table_id VARCHAR,source_key VARCHAR,reason VARCHAR)"
    )
    if issues:
        db.executemany(
            "INSERT INTO meta.source_relation_issues VALUES (?,?,?)", sorted(set(issues))
        )
    return replace(bindings, entries=(*bindings.entries, *additions))
