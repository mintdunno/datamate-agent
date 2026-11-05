# ĐẶC TẢ CHUẨN HOÁ DỮ LIỆU DATAMATE — MỌI TRƯỜNG HỢP, MỌI THỂ LOẠI DỮ LIỆU

*Phiên bản 2026-11-04 (v3 — bản tổng quát). Phạm vi: **bất kỳ business nào**, không giới hạn ở các shop đã thấy. Đầu vào duy nhất được phép giả định là: (1) các file dữ liệu của business đó, (2) `rulebook.md` của split, (3) `README.md` của business đó. Mọi khác biệt giữa các business — ngành nghề, tên cột, định dạng số, tập trạng thái, đơn vị, thực thể riêng của ngành — đều phải được **khám phá từ ba đầu vào trên**, không được ghi cứng từ các business đã biết. Các nhóm A–K ở dưới là catalog trường hợp đã gặp thật khi giải 16 business (dev_001–dev_014 + biz_106/biz_120, 1.237 câu, bộ Hello có tuyên bố bên ngoài đạt 237/237, chưa được repository này kiểm chứng) — chúng là **minh chứng và bộ kiểm thử hồi quy** cho các luật tổng quát ở Phần I, không phải giới hạn phạm vi. Các tệp `schema.sql`, `validation.sql` và chi tiết A10 được nhắc đến nhưng chưa tìm thấy; phải định nghĩa và kiểm chứng hợp đồng trước khi dùng.*

**Revision 4 (2026-11-05):** Polars/pure Python xử lý typed record batches; DuckDB cho storage/query/spill. Không thêm Pandas. Preparation không đọc evaluator aliases/answers. Số liệu lịch sử là tuyên bố bên ngoài chưa được repository kiểm chứng; không dùng làm target tuning. Unknown semantics chặn scope phụ thuộc; chỉ reuse facts qua rule changes khi chứng minh độc lập. Khả năng mở rộng qua khai báo không bảo đảm mọi ngành mới không cần typed extension/schema hoặc executor mới.

## PHẦN I — KHUNG TỔNG QUÁT (áp cho mọi business, kể cả ngành chưa từng thấy)

### I.1 Năm nguyên tắc bất biến

1. **Lưu nguyên tử, không lưu kết luận.** Tầng lưu trữ giữ mọi thành phần thô đã parse (từng loại tiền: thành tiền dòng, giảm giá dòng, giảm giá đơn, ship, thu máy cũ…; mọi trạng thái đã map; mọi khoản thanh toán kèm ngày) và **không** lưu sẵn các kết luận phụ thuộc luật như “đơn hợp lệ” hay “doanh thu” dưới dạng cột chết. Các kết luận đó được sinh ra ở tầng truy vấn từ `meta.measure_definitions` biên dịch từ rulebook — thay đổi chỉ ở measure tái sử dụng fact sạch khi chứng minh độc lập; thay đổi parse/danh tính/điền nhóm/khử trùng/trạng thái phải dựng lại fact liên quan. (Ngoại lệ thực dụng duy nhất: các cột suy diễn *thuần cấu trúc* như `order_date` tách từ timestamp, `lines_total` = tổng dòng của chính đơn đó.)
2. **Không ghi cứng gì thuộc về một business/ngành cụ thể.** Không giả định: vị trí header, tiền tố mã (SP/KH chỉ là ví dụ), tập từ trạng thái, đơn vị tiền tệ, tên ngành, sự tồn tại của bất kỳ bảng/cột nào. Mọi thứ thuộc loại này đều nằm trong các bảng registry (`compiled_rules`, `readme_declarations`, `column_roles`, `status_map`, `unit_conversions`, `business_tables`, `entity_types`) — thay business là thay nội dung registry, không thay code và không thay schema.
3. **Khám phá bằng tín hiệu, chốt bằng bằng chứng.** Profiler đề xuất bằng tín hiệu đo được (tỉ lệ parse, độ duy nhất, cardinality, khớp công thức); luật/README ghi đè; không đủ bằng chứng → quarantine. Thứ tự ưu tiên như Nhóm 0.
4. **Ưu tiên khai báo cho semantics đã hỗ trợ.** Ngành mới xuất hiện thực thể mới (vd một phòng khám có “lượt khám”, một tiệm cho thuê có “lượt thuê”): khai báo loại thực thể + khoá danh tính + thuộc tính vào `meta.entity_types` và lưu vào `clean.entities` / `entity_links` dùng chung — schema không đổi. Semantics mới có thể cần typed extension/schema hoặc operator mới; chưa hỗ trợ thì giữ staging/PARTIAL thay vì ép thành JSON clean entity.
5. **Mọi khái niệm rulebook không hiểu được phải lộ diện.** Điều luật nào trong rulebook không biên dịch được vào registry (khái niệm lạ, tham chiếu thiếu) → ghi vào `compiled_rules` với `parameters.status='UNMAPPED'` + chặn scope phụ thuộc; chỉ chặn cả split khi phụ thuộc phủ toàn split. Im lặng bỏ qua một điều luật là lỗi nghiêm trọng nhất của pipeline tổng quát.

### I.2 Khám phá cấu trúc: nguyên mẫu bảng (archetype)

Mọi bảng nguồn được phân vào một **nguyên mẫu** bằng tín hiệu, tên file chỉ là gợi ý yếu:

| Nguyên mẫu | Tín hiệu nhận diện | Đích lưu canonical |
|---|---|---|
| `transaction_header` | Một dòng/đơn, có mã đơn + ngày + trạng thái | `clean.orders` (phần cấp đơn) |
| `transaction_lines` | Nhiều dòng/đơn, có mã hàng + SL + thành tiền | `clean.order_lines` |
| `transaction_combined` | Một bảng vừa cấp đơn vừa dòng hàng (kiểu POS phổ biến nhất) | tách thành orders + order_lines theo grain (C6) |
| `catalog` | Một dòng/thực thể, có mã + tên + giá/đơn vị gốc | `clean.products` hoặc `clean.entities` |
| `reference_list` | Danh sách khách/NCC/nhân viên kèm thuộc tính | bảng dimension tương ứng hoặc `clean.entities` |
| `cash_book` | Dòng thu/chi kèm ngày + số tiền + tham chiếu đơn/khách | `clean.payments` |
| `stock_ledger` | Dòng nhập/xuất/tồn kèm mã hàng ± lô ± hạn dùng | `clean.inventory_lots` / imports |
| `price_list` | Mã hàng + giá + (ngày hiệu lực) | `clean.price_history` |
| `conversion_table` | Cặp đơn vị + hệ số | `meta.unit_conversions` |
| `calendar` | Ngày + nhãn kỳ nghỉ/kỳ đặc biệt | `clean.calendar_days` |
| `unknown` | Không khớp tín hiệu nào | Giữ raw/provenance ở staging; JSON thô không tự thành clean entity. Scope phụ thuộc PARTIAL cho tới khi có bằng chứng |

### I.3 Ontology vai trò cột — mở, không đóng cứng

Danh sách vai trò ở Nhóm B là hạt giống. Vai trò **mới** được đăng ký vào `meta.role_definitions` (tên vai trò, loại giá trị, mô tả, nguồn: rulebook/README) khi rulebook hoặc README nhắc một khái niệm chưa có — vd “ngày hết hạn bảo hành”, “số ghế”, “ca trực”. Profiler chấm điểm cho cả vai trò mới nếu mô tả của nó cho tín hiệu đo được; không đo được thì gán bằng khai báo README.
Tương tự, **từ vựng chuẩn của trạng thái/kênh/thanh toán không cố định ở schema**: tập giá trị chuẩn lấy từ rulebook của split (mặc định thương mại: completed/cancelled/returned/delivering), đăng ký trong `compiled_rules.parameters`; cột `status_canonical` không bị khoá bằng CHECK của DDL nữa — ràng buộc nằm ở tầng registry để split khác dùng tập chuẩn khác mà không phải sửa schema.

### I.4 Ngữ pháp parse tổng quát — khám phá theo cột, không theo shop

- **Tiền/số:** bộ ngữ pháp ứng viên gồm: phân cách nghìn ∈ {`.`, `,`, khoảng trắng, không có}, dấu thập phân ∈ {`,`, `.`}, hậu tố thang ∈ {k, nghìn, tr, triệu, tỷ, đ, đồng, VND, ký hiệu tiền tệ khác}, dạng âm ∈ {dấu trừ, ngoặc đơn, số lượng âm mang nghĩa trả}. Với mỗi cột, thử mọi tổ hợp ứng viên, chấm bằng (a) tỉ lệ parse và (b) tỉ lệ khớp công thức tiền của cột đó; chỉ chọn tổ hợp có bằng chứng phân biệt độc lập; các thang đo cùng hợp lệ phải để unresolved, và được ghi vào manifest của cột. Không có tổ hợp nào đạt → quarantine, không chọn bừa.
- **Ngày/giờ:** ứng viên gồm datetime thật của Excel, **số serial Excel lưu dạng text/số** (đã gặp ở dev_007: ngày giao dịch trộn dd/mm/yyyy và serial), ISO, `dd/mm/yyyy` và biến thể, dạng chữ tháng tiếng Việt (`9 thg 9 2025`), dạng chỉ tháng/năm. Chấm bằng tỉ lệ parse + epoch/format và đối chiếu độc lập; ngày không cần tạo chuỗi liên tục.
- **Định danh:** mẫu tiền tố mã được *học* từ dữ liệu (cụm ký tự lặp lại ở đầu các giá trị gần-duy nhất), không giả định SP/KH/ORD.

### I.5 Thực thể và liên kết — tổng quát

- `meta.entity_types` khai báo từng loại thực thể của business: loại lõi (product, customer, supplier, staff) + loại riêng của ngành do rulebook §6/README khai báo (vehicle, device, lot, patient, room…), kèm **khoá danh tính** của loại đó (cột mã nào, hoặc khoá tên chuẩn hoá, hoặc khoá liên kết).
- Thực thể lõi thương mại lưu ở các bảng dimension chuyên dụng; thực thể khác chỉ qualify khi có typed factual schema phù hợp; JSON dành cho raw/metadata/thuộc tính hiếm, không thay mọi query facts.
- **Liên kết thực thể** (hai tên gọi là một thực thể nhờ một khoá chung — biển số nối dòng xe, IMEI nối máy, mã vạch nối hai bảng…) khai báo bằng `link_key` trong entity_types và lưu ở `clean.entity_links` (hai mã + loại khoá + bằng chứng). Phép hợp nhất cụm dùng union-find trên các liên kết đã khai báo — cơ chế này tổng quát hoá F7 cho mọi ngành.
- Đồng nghĩa vẫn theo luật bằng chứng F4, với tập bằng chứng mở: bất kỳ trường nào rulebook/README chỉ định làm căn cứ khớp (lịch sử giá, khoá chung, bảng đối chiếu) đều dùng được.

### I.6 Định nghĩa đo lường biên dịch từ rulebook

`meta.measure_definitions` lưu, cho từng split/business: tên đo lường (revenue, debt_outstanding, inventory_value, cancel_rate…), bảng nguồn, vai trò cột tiền, tập trạng thái hợp lệ, các thành phần gồm/loại (ship, giảm giá đơn, VAT…), mốc thời gian theo cột ngày nào. Tầng truy vấn đọc bảng này để tính; tầng lưu trữ **không** cần biết đo lường nào tồn tại. Chỉ reuse facts nếu dependency audit chứng minh rule mới không ảnh hưởng diễn giải nguồn; thay đổi parse/identity/grain/status phải rebuild facts liên quan.

### I.7 Ví dụ xuyên ngành (minh hoạ cơ chế, không phải danh sách đóng)

Một business **cho thuê xe** chưa từng thấy: README khai báo bảng “phiếu thuê” (transaction_combined), thực thể `vehicle` nối bằng biển số (entity_links), cột “tiền cọc” là vai trò mới đăng ký vào role_definitions, rulebook của split định nghĩa doanh thu không gồm tiền cọc → measure_definitions ghi loại cọc khỏi revenue, tiền cọc vẫn lưu thành cột thành phần riêng. Các semantics đã hỗ trợ chạy bằng registry; khái niệm chưa hỗ trợ cần typed extension/operator được review trước khi qualify. Nếu rulebook của split đó không nhắc tiền cọc, khai báo của README vẫn đủ để lưu nó đúng chỗ, và phần đo lường liên quan đến cọc sẽ bị gắn cờ UNMAPPED thay vì tự đoán.

## 0. Kiến trúc lưu trữ 3 lớp

| Lớp | Nội dung | Ai được ghi |
|---|---|---|
| `staging.raw_rows` | Toàn bộ dòng thô, payload JSON giữ nguyên văn, kèm file/sheet/số dòng | Chỉ loader |
| `clean.*` | Dữ liệu sạch theo schema canonical trong `schema.sql` | Chỉ pipeline chuẩn hoá |
| `meta.*` | **Luật đã biên dịch (`compiled_rules`) + khai báo README (`readme_declarations`)**, manifest vai trò cột, status_map, conversion graph, summary_rows (mỏ neo), quarantine, cleaning_audit | Pipeline chuẩn hoá |

**Quy tắc kiểu dữ liệu bất biến:**
- Tiền VND: Decimal lossless, precision/scale theo nguồn trong giới hạn DuckDB tới 38 chữ số; vượt giới hạn giữ raw/chặn scope, không dùng float hay cắt scale.
- Số lượng: Decimal lossless, precision/scale theo nguồn trong giới hạn hỗ trợ (đơn vị gốc có thể lẻ: kg, lít, mét). Số lượng thô và số lượng đã quy đổi lưu **song song**, không ghi đè.
- Ngày: `DATE`; thời điểm: `TIMESTAMP` (null nếu nguồn chỉ có ngày — và ghi `businesses.has_time_of_day = false`).
- Mã: `VARCHAR` giữ nguyên tiền tố (SP…, KH…, ORD…), **giữ mã raw; tạo mã trimmed riêng khi có bằng chứng và kiểm tra collision** (ID có khoảng trắng thừa làm join silently fail — đã gặp ở dev_010).
- `name_norm` (chỉ dùng truy hồi ứng viên) = NFC → thường hoá → bỏ dấu tiếng Việt (kể cả đ→d) → gộp khoảng trắng → cắt hai đầu; khoá này chỉ tạo ứng viên, không tự chứng minh danh tính.

## NHÓM 0 — LỚP LUẬT: rulebook và README quyết định chuẩn hoá (đọc trước mọi nhóm khác)

Chuẩn hoá ở đây **không phải** suy luận thuần từ dữ liệu. Mọi quyết định “cái gì được xoá, cái gì được gộp, cột nào có nghĩa là gì” đều phải truy được về một trong hai nguồn luật, theo thứ tự ưu tiên cứng:

**rulebook của split (luật chung) → README của shop (khai báo riêng) → heuristic profiler → phán đoán của model/người.**

Xung đột giữa các nguồn: nguồn cao hơn thắng, và xung đột phải ghi vào `meta.column_roles.evidence` / `meta.cleaning_audit.detail` — không im lặng chọn một phía. Rulebook được biên dịch một lần thành `meta.compiled_rules` (kèm trích dẫn nguyên văn), README được tách thành từng khai báo trong `meta.readme_declarations` (kèm trích dẫn nguyên văn). Hai bảng này là một phần của dữ liệu đã chuẩn hoá, nạp cùng DuckDB.

**Nơi lưu trữ — quyết định dứt khoát:** toàn văn rulebook và README được lưu **trong DB** ở `meta.source_documents` (kèm sha256 + version; loader so hash để bỏ qua biên dịch lại khi tài liệu không đổi), và mọi hàng trong `compiled_rules` / `readme_declarations` đều trỏ về `source_doc_id` của nó. Rulebook/README **không** đưa vào system prompt: chúng là dữ liệu theo split/theo business, thay đổi độc lập với nhau, và system prompt chỉ chứa *quy trình* (cách biên dịch luật, thứ tự ưu tiên, chính sách abstain). Lúc chạy, model chỉ nhận lát cắt đã biên dịch liên quan đến việc đang làm (compiled rules + declarations + measure definitions của business đó), không nhận toàn văn — vừa rẻ token, vừa truy vết được, vừa tránh trộn luật của các split khác nhau.

### 0.1 Rulebook chi phối chuẩn hoá theo từng điều

| Điều | Quyết định chuẩn hoá bị chi phối |
|---|---|
| §1 Đơn hàng & doanh thu | Giữ mọi đơn/trạng thái. Measure doanh thu loại đơn huỷ và cả đơn trả dù trả một phần, gồm đang giao; cộng thành tiền dòng đã trừ giảm giá dòng, không trừ giảm giá đơn, không cộng VAT/ship. Kỳ theo ngày đặt; mẫu số huỷ gồm mọi đơn đặt. Không lưu orders.is_valid toàn cục |
| §2 Ngữ nghĩa thời gian | Cách nói tương đối theo as_of_date; tháng/quý không năm dùng năm hiện tại nếu kỳ không sau hôm nay, ngược lại năm trước. Ngày NGUỒN thiếu năm dùng năm sheet (§8). Giữ giờ khi có; Tết theo năm hỏi và calendar.csv của split |
| §3 Danh tính & đếm | Danh tính theo **mã** (F1/F2), phí phục vụ loại khỏi đếm mặt hàng (F6), số lượng tính theo **đơn vị gốc** (bắt buộc có `qty_base`, Nhóm G), hoà hạng xếp theo mã → mã là khoá chính và khoá sắp xếp, không bao giờ vứt |
| §4 Công nợ | Giữ từng payment/ngày. Nợ theo as_of gồm đơn không huỷ, kể cả trả nếu còn nợ; loại COD và đơn qua sàn TMĐT. Căn cứ nợ gồm giảm giá đơn và phí giao, khác doanh thu |
| §5 Nhập hàng, tồn kho, giá | Khử trùng phiếu nhập theo mã phiếu (D3); phiếu đầu kỳ vẫn tính (H6); giá trị tồn kho theo **giá vốn** (H4); hiệu ứng đổi giá cần bảng `price_history` (H5) |
| §6 Module theo ngành | §6 quyết định business thuộc ngành nào phải có bảng/ thuộc tính mở rộng nào: nhà thuốc → lô + hạn dùng + hoạt chất; VLXD → bảng giá theo ngày + đợt giao; điện thoại → IMEI + trả góp; sửa xe → phiếu sửa + thợ. `meta.businesses.industry` lấy từ đây và từ README |
| §7 Không trả lời được | Tách coverage nguồn khai báo, ngày quan sát, event history và snapshot validity. Nguồn vắng mặt có thể unanswerable; lỗi parse/quarantine/chưa map là bất định xử lý, không phải nguồn vắng mặt hay tổng bằng 0 |
| §8 Cách ghi trong file | Chỉ khử trùng hai dòng cùng đơn giống mọi ô. Hai đơn khác nhau và bảng không ghi theo đơn giữ dòng giống nhau. Điền ô trống chỉ trong cùng nhóm có bằng chứng. Summary/footer cần evidence/audit cấu trúc; không suy quyền xoá chung từ §8 |
| §9 Định dạng đáp án | Giữ biến thể tên quan sát từ file nguồn với provenance. Preparation không đọc accepted aliases/reference answers/evaluator metadata; evaluator giữ riêng |

### 0.2 README của shop chi phối chuẩn hoá theo từng khai báo

| Loại khai báo trong README | Ví dụ thật | Hiệu lực lên chuẩn hoá |
|---|---|---|
| Khai báo vai trò cột/bảng (“file X là sổ…”, “cột Y là…”) | Mọi shop | Ghi đè phỏng đoán profiler ở B1; lưu vào `column_roles` với `decided_by='readme'` |
| Bảng quy đổi được khai báo trong README | dev_010: tỉ lệ thùng/lốc trong bảng README khớp tuyệt đối SL quy đổi; dev_004: luật bó Ống PVC 1 bó = 10 cây nằm ở README + danh mục | Tạo cạnh trong `unit_conversions` với `source='readme'` trước khi suy luận từ dữ liệu |
| Định nghĩa căn cứ công nợ riêng của shop | biz_106: “Tổng sau giảm giá” (gồm ship) là căn cứ nợ theo README + §4 | Sinh cột `total_after_discount` tách khỏi `lines_total` (H2) |
| Ngữ nghĩa cột text tự do | dev_008: README nói Note là trạng thái nợ tại cuối dữ liệu | `debt_outstanding` của dev_008 có ngữ nghĩa snapshot cuối kỳ, `debt_source='note_pattern:…'`; shop khác không có khai báo này thì không áp |
| Phạm vi sổ quỹ | biz_106: SoQuy chỉ có thu, không có chi | Chỉ nạp payments thu (H7); cấm suy chi phí |
| Luật tách/gộp riêng | dev_002: tách dòng topping gộp theo README | Luật tách ở B3/F9 lấy nguồn `readme`, ghi rõ trong audit |
| Cách viết tên được chấp nhận | biz_120: tên khách ở danh sách có thể dính số điện thoại so với trên đơn | Mọi biến thể đều vào `entity_aliases`; giữ tên trên đơn như một biến thể nguồn; không dùng đáp án chuẩn làm bằng chứng chuẩn hoá |

**Hệ quả kiến trúc:** một pipeline chuẩn hoá không đọc rulebook + README trước thì chỉ là “làm sạch theo cảm tính dữ liệu” — nó sẽ xoá đơn huỷ (sai mẫu số §1), cắt giờ bán (sai ca §2), gộp đồng nghĩa theo độ giống tên (sai §3/F4) và tổng hợp sẵn công nợ theo một mốc (sai §4). Vì vậy trong quy trình chạy thật, biên dịch luật (Nhóm 0) là bước **trước** Nhóm A.

## NHÓM A — Cấu trúc file / sheet

| # | Trường hợp (ví dụ thật) | Phát hiện | Luật chuẩn hoá | Lưu vào | Kiểm chứng |
|---|---|---|---|---|---|
| A1 | Nhiều file tháng cho cùng một bảng (dev_002: 17 file; dev_014: 15 sheet tháng) | Tên file/sheet theo mẫu tháng | Nối tất cả vào một bảng thô theo `table_role`; tên sheet/file không thay ngày đặt để tính kỳ; năm sheet có thể bổ khuyết NGÀY NGUỒN thiếu năm theo §8 | `staging.raw_rows` + provenance | Tổng số dòng thô = tổng mọi file |
| A2 | Header không ở dòng 1 (dòng 3 ở biz_106; dòng 4 ở dev_001/002/005) | Chấm điểm từng dòng: số ô khớp từ điển tên cột + độ chuyển kiểu ở dòng dưới | Chọn dòng điểm cao nhất làm header; dòng phía trên vào audit `drop_preamble` | `meta.column_roles.header_row` | Mọi cột bắt buộc phải có vai trò |
| A3 | Header lặp giữa file (dev_010: 226 dòng) | Dòng bằng đúng vector header | Loại, ghi audit `drop_repeat_header` | audit | Phương trình đối chiếu dòng (mục 4) |
| A4 | Dòng trống, dòng tiêu đề trang trí | Mọi ô rỗng / ô gộp chứa tiêu đề | Loại, ghi audit | audit | Như A3 |
| A5 | Dòng tổng ngày/tháng lẫn trong dòng bán (587 dev_008; 554 dev_001; 540 dev_009; 616 dev_007; 511 dev_010) | Mã đơn rỗng **hoặc** nhãn khớp `tổng ngày/cộng ngày/tổng tháng/tổng cộng` **và** số tiền = tổng khối ngày/kỳ đó (kiểm chứng số học trước khi loại) | Loại khỏi fact, **chuyển nguyên vẹn** sang mỏ neo | `meta.summary_rows` | G1.3: tổng tính lại từ dòng sạch phải khớp 100% các mỏ neo |
| A6 | Footer tổng chốt cuối sheet tháng (dev_014: 15 footer) | Dòng cuối sheet khớp mẫu tổng + bằng tổng sheet | Như A5 với `period_kind='sheet_footer'` | `meta.summary_rows` | Tổng sheet tính lại = footer, 15/15 |
| A7 | Dòng tiếp nối để trống trường cấp đơn (dev_005) | Trong một khối đơn, dòng sau trống mã đơn/ngày/trạng thái | Forward-fill **chỉ trong cùng file + cùng khối đơn**; cấm điền qua ranh giới file | `clean.orders/order_lines` | Trường cấp đơn không đổi trong một đơn (0 vi phạm) |
| A8 | Nguồn không phải Excel: CSV có encoding/delimiter lạ; SQLite (dev_006) | Phần mở rộng + probe parse | CSV: utf-8-sig mặc định, dò delimiter; SQLite: đọc bảng trực tiếp vào staging với `table_role` tương ứng | `staging.raw_rows` | Như A1 |
| A9 | Thiếu hẳn một bảng/loại thông tin (biz_106 không có dữ liệu lô/hạn dùng; dev_004 không có cột nhóm) | Profiler không tìm thấy bảng/cột | **Ghi nhận sự vắng mặt** (cột để NULL, `businesses.notes`), không tạo dữ liệu thay thế — sự vắng mặt là căn cứ `unanswerable` về sau | `clean.*` (NULL) + `meta.businesses` | Audit ghi rõ bảng/cột vắng |

## NHÓM B — Cột và vai trò

| # | Trường hợp | Phát hiện | Luật chuẩn hoá | Lưu vào | Kiểm chứng |
|---|---|---|---|---|---|
| B1 | Cùng vai trò, tên cột khác nhau giữa các shop (“Giờ bán” / “Ngày bán” / “Ngày tạo”…) | Profiler chấm điểm vai trò (tỉ lệ parse ngày/tiền, độ duy nhất, cardinality) + trích README | Gán vai trò từ danh sách đóng; README là bằng chứng ưu tiên; điểm sát nhau → người/model duyệt (điểm chạm C3) | `meta.column_roles` | Vai trò đã chọn phải cho tỉ lệ parse cao nhất |
| B2 | Cột lạ không có vai trò canonical (cột riêng của ngành) | Không khớp vai trò nào | Không bỏ: đưa vào `attributes` JSON của bảng chủ | `clean.*.attributes` | Đếm cột nguồn = cột có vai trò + cột vào attributes |
| B3 | Một cột chứa hai trường (ô 2 IMEI — xem J5; tên khách dính số điện thoại — xem F3) | Regex mẫu trong ô | Tách theo luật riêng của từng mẫu (Nhóm J/F) | bảng con / alias | Bảo toàn: tách xong không mất ký tự có nghĩa |
| B4 | Cột “đã quy đổi sẵn” tồn tại song song cột thô (dev_001: “Số lượng quy đổi”) | Hai cột SL, một cột khớp đơn vị gốc | Ưu tiên cột quy đổi của nguồn cho `qty_base` **nhưng** phải đối chiếu với đồ thị quy đổi (Nhóm G); lệch → quarantine | `clean.order_lines` | Hai đường quy đổi khớp nhau |

## NHÓM C — Parse giá trị

| # | Trường hợp (dạng thật đã gặp) | Luật parse | Lưu vào | Kiểm chứng chọn parser |
|---|---|---|---|---|
| C1 | Tiền dạng số sạch `25000` | Đọc thẳng | DECIMAL(p,s), p≤38, scale theo nguồn | Công thức dòng (G1.1) |
| C2 | Tiền nghìn phân cách chấm + hậu tố: `532.000đ`, `10.000đ`, `0đ` | Bỏ hậu tố đ/đồng, chấm = phân cách nghìn | DECIMAL(p,s), p≤38, scale theo nguồn | G1.1 |
| C3 | Tiền viết tắt triệu: `4,544tr`, `294,7703tr`, `8tr399` (= 8.399.000) | `X,Ytr` = X,Y × 1.000.000 (phẩy thập phân); `XtrYYY` = X.YYY.000 | DECIMAL(p,s), p≤38, scale theo nguồn | G1.1 + neo tổng phiếu |
| C4 | Tiền cách bằng khoảng trắng: `16 076` (giá vốn danh mục) | Khoảng trắng = phân cách nghìn khi phần sau đúng 3 chữ số | DECIMAL(p,s), p≤38, scale theo nguồn | Neo giá vốn/tổng kho |
| C5 | Hậu tố `k/nghìn` trong ghi chú nợ: `nợ 500k` | × 1.000 | DECIMAL(p,s), p≤38, scale theo nguồn | Kiểm chứng số học của mẫu nợ (Nhóm I) |
| C6 | Số âm: dòng trả hàng (dev_004), giảm giá | Giữ dấu; số lượng âm ở dòng bán = dấu hiệu trả hàng (đối chiếu H4) | DECIMAL có dấu | G1.1 với dấu |
| C7 | Ngày dạng datetime thật của Excel | Đọc thẳng, tách `order_date` + giữ giờ | TIMESTAMP + DATE | Khoảng ngày hợp lý |
| C8 | Ngày text `dd/mm/yyyy`, `d/m/yyyy` | Parse theo định dạng đã chọn cho cột | DATE | Tỉ lệ parse ~100% |
| C9 | Ngày text dạng chữ `9 thg 9 2025` | Parser riêng “d thg m yyyy” | DATE | Như C8 |
| C9b | Ngày dạng **số serial Excel** trộn lẫn ngày text trong cùng cột (dev_007: cột ngày giao dịch) | Nhận diện số nguyên/thập phân trong khoảng serial hợp lệ (tương ứng khoảng ngày của shop) → đổi serial → DATE; phần còn lại parse theo C8; epoch/format phải được chứng minh; chuỗi ngày không cần liên tục | DATE | Epoch/format và phạm vi có bằng chứng; không yêu cầu ngày liên tục |
| C10 | Ngày chỉ có tháng/năm ở cấp sheet (tên sheet là tháng) | Không suy ngày cho từng dòng; ngày dòng lấy từ cột ngày thật, sheet là provenance và có thể cung cấp năm cho ngày nguồn thiếu năm theo §8 | DATE + provenance | Không có dòng nào thiếu `order_date` |
| C11 | Số lượng thập phân phẩy/chấm; số lượng lẻ theo đơn vị gốc | Chuẩn hoá dấu thập phân theo cột | DECIMAL(p,s), p≤38, scale theo nguồn | G1.1 |
| C12 | ID có khoảng trắng thừa / khác hoa thường (dev_010) | Trim + giữ nguyên dạng còn lại; join trên giá trị đã trim | VARCHAR | Tỉ lệ join thành công (G1.5) |

## NHÓM D — Dòng trùng và dòng đặc biệt

| # | Trường hợp | Luật | Kiểm chứng |
|---|---|---|---|
| D1 | Dòng trùng **y hệt mọi cột** trong cùng đơn (§8) — 3.230 dòng biz_106; 1.994 dev_003; 1.381 dev_007; 1.350 dev_006; 308 đơn dev_009; 175 dev_014; 166 dev_013; 0 ở biz_120/dev_002 | Giữ đúng 1 bản; ghi audit số dòng loại theo từng bảng | Sau khử trùng: G1.2/G1.3 khớp 100% (trước khử trùng biz_106 lệch 3.197 đơn) |
| D2 | Dòng **gần** trùng (khác ít nhất một cột) | **Giữ nguyên** — là giao dịch thật; chỉ báo cáo trong audit | Không có luật xoá |
| D3 | Trùng ở cấp phiếu khi tính tổng nhập (cộng cả dòng lẫn tổng phiếu = đúp) | Tổng phiếu lấy đúng grain từ header và đối chiếu tổng dòng; cùng `import_id` không tự cho phép xoá dòng; chỉ khử trùng dòng cùng đơn/phiếu giống mọi ô với bằng chứng lỗi xuất file | G1.8 |
| D4 | Đơn trả thể hiện bằng dòng số lượng âm (dev_004) | Trạng thái đơn suy ra `returned` **chỉ khi** đã kiểm chứng khớp 1-1 với trạng thái chữ trên toàn bộ dữ liệu | Đối chiếu hai tập đơn: khớp tuyệt đối |
| D5 | Đơn trả nằm ở file lô riêng (dev_005: DonHang_Lo 354 đơn; biz_120: 426 đơn) | `returned_qty` nạp từ file lô theo (đơn, mã hàng); tập đơn có trả > 0 phải khớp tập đơn trạng thái trả | Hai tập khớp 100% |

## NHÓM E — Trạng thái, kênh, phương thức thanh toán

Tập chuẩn của split thương mại hiện tại: trạng thái ∈ {completed, cancelled, returned, delivering, unknown}; predicate của measure doanh thu chọn {completed, delivering} theo rulebook train, không tạo cột `is_valid` toàn cục. Theo nguyên tắc I.3, tập chuẩn này **không khoá cứng trong schema** — nó được đăng ký trong `compiled_rules` của split; split dùng tập trạng thái chuẩn khác thì chỉ thay registry. Bảng hạt giống dưới đây là từ vựng *ứng viên* để khớp mờ, không phải danh sách đầy đủ vĩnh viễn: giá trị lạ ngoài bảng này đi qua điểm duyệt C8 như thường lệ.

**Bảng hạt giống status_map** (tổng hợp mọi biến thể đã gặp thật — shop mới có biến thể lạ thì bổ sung qua điểm duyệt C8, không đoán):

| Chuẩn | Biến thể đã gặp |
|---|---|
| completed | đã xong, Đã xong, Thành công, đã lấy, Hoàn thành, Đã giao, Đã hoàn thành, ok, Xong, Đã nhập hàng (phiếu nhập), received |
| cancelled | khách hủy, hủy đơn, Đã hủy đơn, Đã huỷ, huỷ, Không giao được |
| returned | Hoàn trả, trả hàng, Đã hoàn hàng, trả lại, Trả hàng, Đã hoàn thành trả (theo README từng shop) |
| delivering | Đang giao, Đang giao dịch, Đang giao hàng, Đang xử lý |
| unknown | mọi giá trị còn lại — **không** được tính là đơn hợp lệ, phải xuất hiện trong audit |

| # | Trường hợp | Luật |
|---|---|---|
| E1 | Kênh bán nhiều dạng (tại quầy/quầy, điện thoại, zalo OA, online…) | `channel_raw` giữ nguyên; `channel_canonical` ∈ {store, phone, zalo_oa, online, other} theo từ điển từng shop; không có cột kênh → NULL (căn cứ unanswerable cho câu hỏi kênh) |
| E2 | Thanh toán: tiền mặt/chuyển khoản/ví điện tử/ghi nợ/COD/thẻ | `payment_method_canonical` ∈ {cash, transfer, ewallet, debt, cod, card, other}; đơn `debt` và không phải COD là căn cứ tính công nợ (§4) |
| E3 | Trạng thái phiếu nhập: tất cả “Đã nhập hàng”, không có phiếu huỷ (dev_008, biz_106/120) | Vẫn map qua status_map; `imports.status_canonical` cho phép rỗng tập cancelled — không giả định có |

## NHÓM F — Danh tính thực thể

Thứ tự ứng viên: **mã có scope → tên chuẩn hoá → đồng nghĩa**; chỉ chấp nhận sau kiểm tra cardinality, scope và collision với bằng chứng độc lập. Mọi lần gộp đều ghi vào `clean.entity_aliases` kèm `evidence_type`.

| # | Trường hợp (ví dụ thật) | Luật chuẩn hoá | Bằng chứng bắt buộc |
|---|---|---|---|
| F1 | Tên hàng ~100 biến thể cho một mã (hoa/thường, thừa khoảng trắng, mất dấu: “Coca-Cola  lon”, “Cha ca chien”) | Danh tính = `product_code`; tên hiển thị = tên xuất hiện nhiều nhất của mã | same_code |
| F2 | Khách nhiều tên cho một mã (tên trơn / tên + điện thoại) | Danh tính = `customer_code`; giữ tên modal | same_code |
| F3 | Không có mã: gộp theo `name_norm` (dev_001: 74→60 SP; dev_005: 105→80 SP) | name_norm chỉ tạo ứng viên; kiểm tra scope, cardinality và xung đột hai chiều, kể cả va chạm do bỏ dấu; chưa phân biệt được thì unresolved | name_norm + kiểm tra xung đột |
| F4 | Đồng nghĩa địa phương: Đậu phụ rán giòn = Đậu hũ chiên giòn; Má lợn = Má heo; Canh chua cá tràu = cá lóc; Cháo ngao = nghêu; Mực rán = chiên; Lạc rang muối = Đậu phộng rang muối; Cát xây trát = Cát xây tô; Găng tay tất chân = Bao tay bao chân; Hoa quả nghiền = Trái cây nghiền; Cốc sạc = Củ sạc nhanh 20W; Kệ = Giá đỡ | Giá/ngày đổi và giống tên chỉ tạo ứng viên, không tự chứng minh danh tính; chỉ gộp khi khoá chung/khai báo nguồn hoặc bằng chứng độc lập phân biệt được, sau kiểm tra scope/cardinality/collision | price_history / shared_key / readme |
| F5 | Khách vãng lai nhiều kiểu viết (khách lẻ / lẻ / kl) | Một thực thể giả `customer_code='WALKIN'`, `is_walkin=true` — loại khỏi mọi đếm/xếp hạng khách | lexicon |
| F6 | Phí phục vụ (SP0061 kiểu) | `products.is_service_fee=true`: vẫn tính doanh thu và mẫu số tỉ trọng, loại khỏi đếm/xếp hạng mặt hàng | rulebook §3 |
| F7 | Dòng xe nhiều tên (dev_012: AB = Air Blade, Alpha = Wave Alpha…) | Union-find theo **biển số xe** xuất hiện chung; tên canonical = tên modal của cụm | shared_key (plate) |
| F8 | Thợ ghi tên gọi tắt (A.Đức, a Phúc…) | Token họ/tên cuối và danh sách thợ chỉ tạo ứng viên; map commission khi scope/cardinality/collision cho một đối chiếu có bằng chứng | staff list + token |
| F9 | Nhóm sản phẩm: có cột nhóm / không có / phải suy từ tên (dev_002: cà phê loại Matcha latte; trà sữa gồm Sữa tươi trân châu đường đen) | Có cột → dùng cột; không có → `product_group=NULL`; suy từ tên chỉ khi rulebook/README yêu cầu và ghi `attributes.group_source='name_rule'` kèm luật đã dùng | readme / rulebook |


## NHÓM G — Quy đổi đơn vị

| # | Trường hợp | Luật | Kiểm chứng |
|---|---|---|---|
| G1 | Quy đổi một bước (bó PVC ×10 — dev_004) | Cạnh trực tiếp trong `meta.unit_conversions` | Mọi đơn vị bán resolve về đơn vị gốc |
| G2 | Quy đổi theo chuỗi (nhà thuốc dev_005: hộp → vỉ → viên qua DanhSachQuyDoi) | Dựng đồ thị, duyệt chuỗi; `conversion_factor` trên dòng = tích cả chuỗi | Không còn đơn vị unresolved (đạt 0) |
| G3 | Quy đổi chỉ áp cho vài mã (biz_106/dev_003: chỉ SP0049/SP0050 có hộp ×6) | Cạnh quy đổi gắn `product_code` cụ thể, không áp toàn shop | Giá vốn theo đơn vị bán suy ra khớp danh mục |
| G4 | Đơn vị gốc lấy từ danh mục (cột DVT) | `products.base_unit` là đích của mọi chuỗi quy đổi | `order_lines.unit_base` = `products.base_unit` |
| G5 | Đơn vị không resolve được | Quarantine cả nhóm, `reason_code=UNIT_UNRESOLVED` — không đoán hệ số | Audit liệt kê |

## NHÓM H — Ngữ nghĩa tiền và tổng

| # | Trường hợp | Luật lưu trữ |
|---|---|---|
| H1 | Doanh thu = Σ thành tiền dòng của đơn hợp lệ; **không** trừ giảm giá cấp đơn, VAT, phí ship (§1) | `orders.lines_total` = Σ dòng; giảm giá đơn/ship/VAT lưu ở cột riêng, không trộn |
| H2 | “Tổng sau giảm giá” **gồm** ship lại là căn cứ công nợ (biz_106: +15.000đ × 7.947 đơn, theo README + §4) | Lưu riêng `orders.total_after_discount` (căn cứ nợ) — khác `lines_total` (căn cứ doanh thu). Hai cột, hai ngữ nghĩa, cấm dùng lẫn |
| H3 | “Khách trả” ở shop điện thoại = Σ thành tiền − Bớt − Thu máy cũ (dev_011/014, khớp 100% đơn) | `customer_paid_reported` giữ số báo cáo; `amount_deducted`, `trade_in_amount` lưu riêng để tái tính và đối chiếu |
| H4 | Giá vốn viết cách khoảng trắng / giá bán hiện tại vs giá vốn (dev_008) | `cost_price` và `sell_price` là hai cột riêng; giá trị tồn kho tính theo **giá vốn** |
| H5 | Lịch sử đổi giá nằm rải trong dữ liệu bán / bảng giá theo ngày (BangGia dev_004) | Tách thành `clean.price_history` (mã, ngày hiệu lực, giá) — đây là bằng chứng khớp đồng nghĩa (F4) và căn cứ câu hỏi hiệu ứng đổi giá |
| H6 | Phiếu nhập đầu kỳ (biz_120: PN00001, 211.648.300đ) | `imports.is_opening=true` nhưng phiếu vẫn tính khi ngày phiếu nằm trong kỳ (§5 — đã được đáp án chuẩn Hello xác nhận) |
| H7 | Sổ quỹ chỉ có thu, không có chi (biz_106 SoQuy) | `clean.payments` chỉ chứa khoản thu thật; **không** suy ra chi phí/lợi nhuận ròng từ sự vắng mặt này |

## NHÓM I — Công nợ và thanh toán

| # | Trường hợp | Luật |
|---|---|---|
| I1 | Nợ ghi trong text Note (dev_008) | Thư viện mẫu: `còn thiếu X`, `thiếu lại X`, `trả A nợ X`, `ghi sổ X`, `nợ cả đơn` → nợ = tổng đơn, `đưa trước A còn lại ghi sổ` → nợ = tổng − A, `đã trả đủ/trả hết` → 0. Lưu `debt_outstanding` + `debt_source='note_pattern:<id>'`. Mẫu lạ → quarantine `NOTE_PATTERN_MISS` |
| I2 | Kiểm chứng mẫu nợ | Nơi ghi cả hai phần: `đã trả + còn nợ = tổng tiền đơn` phải đúng |
| I3 | Nợ tính từ thanh toán (SoQuy/PhieuThu) | `debt_outstanding` = `total_after_discount` − Σ payments của đơn (theo mốc as_of ở tầng truy vấn — lưu trữ giữ **mọi** payment kèm ngày để tính lại cho bất kỳ mốc nào) |
| I4 | Đối chiếu hai đường nợ | G1.9: nợ theo Note/báo cáo phải khớp nợ theo payments; lệch → quarantine đơn đó |
| I5 | Điều kiện đơn được tính nợ (§4) | Đơn không huỷ (đơn trả vẫn tính nếu còn nợ), loại COD và đơn qua sàn TMĐT; không dùng predicate doanh thu làm điều kiện nợ — tầng truy vấn áp thêm `payment_method_canonical` |
| I6 | “Đã trả” báo cáo vs Σ phiếu thu (dev_003: Da tra = Σ PhieuThu khớp chính xác) | G1.10 kiểm tra chéo; đây là mỏ neo cho parser payments |

## NHÓM J — Đặc thù theo ngành (§6)

| # | Ngành / trường hợp | Chuẩn hoá |
|---|---|---|
| J1 | Nhà thuốc — hoạt chất, thuốc kê đơn (dev_005/006, biz_120) | `products.attributes.active_ingredient`, `requires_prescription`; **hoạt chất phối hợp là một loại riêng**, không tách nhỏ; nối qua SKU ↔ tên chuẩn hoá |
| J2 | Nhà thuốc / mẹ & bé — lô hàng, hạn dùng, trả theo lô (dev_005/006) | `clean.inventory_lots` (qty_in, qty_returned, qty_remaining, expiry_date); shop không có dữ liệu lô → bảng rỗng + ghi nhận vắng mặt (biz_106: câu hỏi hạn dùng = unanswerable) |
| J3 | Mẹ & bé — dòng “Cộng”, chiết khấu CK (dev_010: Cộng + CK = Đơn giá × SL trên 100% dòng) | `line_amount` = cột Cộng; CK vào `line_discount`; công thức này là tiêu chí chọn parser cho shop |
| J4 | VLXD — không có cột nhóm; bảng giá theo ngày; bó ×10; trả = SL âm | `product_group=NULL`; BangGia → `price_history`; xem G1, D4 |
| J5 | Điện thoại — ô chứa 2 IMEI “IMEI1 / IMEI2” (dev_014: 135 dòng) | Tách thành 2 dòng `clean.order_line_devices`; bảo toàn: số IMEI sau tách khớp đếm bảo hành |
| J6 | Điện thoại — trả góp (dev_014: 235/235 hợp đồng khớp Giá − Trả trước = Số kỳ × Góp/tháng) | `clean.installments` + kiểm chứng G1.11 |
| J7 | Điện thoại — “dòng máy” = phần tên trước “…GB”; phụ kiện tên gọi tắt (Cốc sạc, Kệ) | `products.attributes.model_line`; tên gọi tắt xử lý như đồng nghĩa F4 (bằng chứng: đối chiếu danh mục) |
| J8 | Sửa xe — thợ theo tên gọi tắt + hoa hồng; phiếu bảo hành tiền 0; token việc (“sửa điện” chỉ tính token sửa điện/làm điện, không tính thay bóng đèn/ắc quy) | `clean.service_tickets` + `clean.staff.commission_rate`; `is_warranty=true` khi ghi chú chứa “bảo hành”; `job_tokens` chuẩn hoá theo từ điển token của rulebook |
| J9 | Hoá đơn dịch vụ (dev_009) — doanh thu ở cột “Giá trị hàng”, TongTien đã gồm điều chỉnh giảm/ship | Vai trò cột theo B1: `line_amount` trỏ “Giá trị hàng”; phần điều chỉnh lưu riêng như H1 |

## NHÓM K — Lịch và thời gian

| # | Trường hợp | Luật |
|---|---|---|
| K1 | `calendar.csv` của đề là nguồn chuẩn duy nhất cho Tết/lễ | Nạp lịch của đúng split; suy Tết năm Y từ ngày dương 23 Chạp (Y−1) đến hết mùng 10 Giêng (Y) theo calendar.csv; không cố định năm/cửa sổ |
| K2 | Câu hỏi theo ca cần giờ; file chỉ có ngày | `businesses.has_time_of_day=false`; giữ `order_ts=NULL` thay vì bịa giờ 00:00 |
| K3 | Tên sheet/file là tháng | Không thay order_date giao dịch; năm sheet có thể dùng cho ngày NGUỒN thiếu năm (§8) |

## 4. Phương trình đối chiếu và cổng G1

Mỗi bảng nguồn phải cân tuyệt đối:

```
số bản ghi thô = tổng các disposition rời nhau; số fact sạch kiểm riêng qua lineage vì một raw có thể tạo 0, 1 hoặc nhiều fact
```

Và toàn bộ truy vấn trong `validation.sql` phải trả về 0 vi phạm (hoặc vi phạm đã có mặt trong `meta.quarantine` kèm lý do): công thức tiền theo dòng, tổng dòng vs tổng đơn, tổng ngày tính lại vs mỏ neo, phủ trạng thái, khoá ngoại, khử trùng theo provenance, đơn không có dòng, tổng phiếu nhập, đối chiếu nợ hai đường, mỏ neo đã trả, bất biến trả góp.

**Nguyên tắc đóng gói:** một business chỉ được coi là “đã chuẩn hoá” khi (1) phương trình đối chiếu cân, (2) cổng G1 đạt, (3) `cleaning_audit` + `quarantine` mô tả được mọi dòng không nằm trong bảng sạch. Thiếu một trong ba, dữ liệu đó chưa sạch — dù nhìn qua có vẻ sạch.
