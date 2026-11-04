# Implementation Contract — DataMate Harness v0.1

> **Loại tài liệu:** Engineering interpretation, **không** phải tab chính thức của Zalo.  
> **Đầu vào:** `../official/*.md` và source files của split.  
> **Model development:** NVIDIA Nemotron 3.5 Bolt E4B qua NVIDIA NIM; **không hợp lệ cho final submission nếu weights phát hành sau cutoff**.  
> **Mục tiêu:** Clean, debuggable, testable baseline; không Embedding/Reranker/Vector DB ở v0.1.

## 1. Context-loading policy cho AI Coding Agent

1. Đọc bộ docs ở `docs/official/` **đầy đủ** (dung lượng nhỏ, chứa requirements).
2. Lập filesystem inventory của `train/` bằng **metadata**, không dump hàng nghìn paths hoặc file contents.
3. Sample ban đầu **tối đa 2 business folders**, **1–2 README**, **5–10 questions và answers**, **≤20 rows/sheet** cho những sheet được khảo sát; CSV ≤20 sample rows; SQLite schema + vài rows.
4. Với rulebook của split đã chọn, phải đọc đủ các điều khoản ảnh hưởng business calculations — không cắt thiếu vì giới hạn sampling của **data rows**. Nếu dài, dùng heading/sections và đọc theo batch.
5. Dừng exploratory inspection khi format đã được hiểu, chỉ đọc thêm có mục tiêu khi gặp format ambiguity.
6. **Runtime loaders không bị sampling cap:** phải xử lý đầy đủ dữ liệu dùng để giải câu hỏi.

## 2. Data preparation contract

- Chọn split → load **split-specific** `rulebook.md` và `calendar.csv`.
- Chọn business → load business `README.md` và sources.
- Preserve immutable raw data; normalize vào tables/views có provenance.
- Không tự ý deduplicate, drop rows, assume currency/date parsing hoặc biến rulebook defaults thành hardcode global.
- Ghi row-count reconciliation, normalization warnings và rule versions.
- Cache preparation theo file fingerprint/rulebook fingerprint; đổi split rulebook phải invalidate cache tương ứng.
- Ground-truth files **không nằm trong DB mà Agent được phép query**.

## 3. Question-solving contract

- Input: `question`, `business_id`, `as_of_date`, metadata schema, split rulebook.
- Agent có tool gọi DuckDB deterministic, search exact/lexical/RapidFuzz, metadata/rule lookup.
- SQL read-only, hạn chế unsafe operations; bounded tool outputs, deadlines và retry budget.
- Relative date luôn dùng **`as_of_date`**, không dùng wall clock.
- Ranking theo explicit question criterion hoặc split rulebook default; valid orders, descending, code ascending.
- Khi dữ liệu không đủ: phân biệt `unanswerable` với `abstain`; chưa tự suy đoán detailed scoring semantics.

## 4. Evaluation contract

- Chỉ Evaluator được mở train `answers.jsonl`; match by `id`.
- Reference có thể chứa `alias`, và `answer:null` cho unanswerable.
- Scorer **MUST NOT** tự giả định comparison semantics: xem `official/evaluation.md` open questions EVAL-01..EVAL-05.
- Có thể implement **format validation và provisional PA** để làm MVP, nhưng cần screenshot/copy chính thức để kết luận “official metric reproduced”.
- Test bằng synthetic fixtures và held-out businesses; test data không dùng làm training.
- Trace mỗi question gồm tool args, SQL, row counts, errors, output, elapsed, model + rulebook version.

## 5. Final competition readiness (sau v0.1)

- Cloud API là development-only; final offline model phải có weights eligible và total inference parameters ≤15B.
- Chạy end-to-end trong Docker với networking disabled, read-only `/data`, `/result/submission.jsonl` và `/result/logs/`.
- Thời gian toàn bộ private workload ≤6h bao gồm loading, với 2× RTX 5090 / 512GB RAM reference.
- Có deterministic outputs, stable tie-breaks và incremental submission writing.

## 6. Unresolved blockers

- [ ] **EVAL-01..EVAL-05:** Missing Evaluation formula/type-matching clauses.
- [ ] **SPLITS-IMAGE:** Diagram splits ở Data tab chưa có.
- [ ] **DOCKER-GUIDELINE:** Separate official Docker guideline chưa có.
- [ ] **PRIVATE-RELEASE-TIME:** 09:00 vs 11:00 ngày 2026-11-20.

## 7. Định nghĩa hoàn thành v0.1

- [ ] Dataset inspection có bounded output.
- [ ] Multi-format data load → DuckDB với provenance.
- [ ] Rulebook được chọn theo split runtime.
- [ ] LLM Agent thực hiện tool calling và SQL-grounded answers.
- [ ] Ground truth isolation hoạt động.
- [ ] Traces theo từng question và tests thực chạy.
- [ ] Validation/evaluator chỉ tuyên bố verified trong phạm vi nguồn đã đủ.
- [ ] Chạy được ít nhất hai businesses khác schema mà không hardcode IDs.
