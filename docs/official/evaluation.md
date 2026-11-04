# Evaluation — Scoring & Matching Contract

> **Source:** Tab **Evaluation** của [Zalo DataMate](https://challenge.zalo.ai/portal/datamate/) do người dùng cung cấp (2026-11-04).  
> **CẢNH BÁO:** Nội dung sao chép bị mất **công thức PA** và phần mô tả/biểu thức **value-matching theo từng type** sau cụm “matches the reference as follows”. Đây là **open blocker** cho exact official scorer.

## 1. Per-question outcomes

Mỗi question có một trong ba outcome: **correct**, **wrong**, hoặc **abstain**.

- Dự đoán có `type: "abstain"` → tính **abstain**.
- Question bị thiếu trong submission → tính **abstain**.
- Với những type còn lại, để là **correct** phải **đúng reference type** và **match reference value** theo luật chính thức. Ngược lại là **wrong**.
- **Không có partial credit**.
- Nếu question là **unanswerable**, việc đưa ra một **substantive answer** bị tính **wrong**.
- Mỗi question có trọng số **như nhau**.

**Chưa đủ nguồn** để mô tả đầy đủ so sánh của từng type (`number`, `percent`, `string`, `date`, `boolean`, `list`, `table`, `unanswerable`): không được tự giả định numeric tolerance, normalization, list order, table row/column matching, alias rules hoặc cách chấm reason.

## 2. Ranking questions — quy tắc đã xác nhận

1. Dùng **criterion nêu rõ trong question**; nếu không có, sử dụng **default trong rulebook của split**.
2. Sắp xếp **descending** theo criterion và chỉ xét **valid orders**.
3. Với các elements có cùng criterion value, tie-break bằng **code ascending**.
4. Questions có tie tại **k boundary** (hoặc **rank 1** cho câu hỏi “… nhất”) **bị loại khỏi test sets** theo tab Evaluation.
5. Nếu có **ít hơn k candidates**, reference ranking gồm **tất cả candidates**.

**Cẩn thận:** Quy tắc exclude tie-at-boundary là quy tắc xây dựng test set/reference, **không** phải cho phép Agent tự ý bỏ câu hoặc bỏ candidates. Hãy vẫn implement tie-break để ranking có thứ tự deterministic.

## 3. Penalized Accuracy (PA)

Leaderboard metric là **Penalized Accuracy (PA)** tính trên toàn bộ `N` questions của test set.

**Công thức chính thức không xuất hiện trong nội dung người dùng paste** (biểu thức nằm sau heading “Score” đã bị mất). Do đó:

- **Không đóng dấu đã verified** cho bất kỳ PA implementation nào chỉ từ tài liệu này.
- Có thể dùng một **provisional local metric** để làm smoke test với giả định correct = +1, wrong = −1, abstain = 0:

  `PA_provisional = (correct_count - wrong_count) / N`

- **Giả định trên cần so với screenshot/evaluation source trước khi triển khai official scorer**. Hãy gắn label `provisional`, đừng gọi là `official_pa`.

## 4. Leaderboard tie-break

Nếu cùng final score:

1. So sánh **total inference time** của organizers' re-run.
2. Nếu vẫn hòa, ưu tiên **submission sớm hơn**.

## 5. Open questions bắt buộc phải giải quyết

| ID | Thiếu gì? | Hậu quả nếu tự đoán | Hành động |
|---|---|---|---|
| EVAL-01 | Chính xác công thức PA trong trang Evaluation (phần công thức bị mất) | Scorer tính sai | Cần ảnh/copy text chứa công thức |
| EVAL-02 | Chính xác value matching cho `number` / `percent` | Sai tolerance/rounding | Cần nội dung sau “as follows” |
| EVAL-03 | Canonicalization cho `string`, `alias`, `date`, `boolean` | Sai reference matching | Cần source gốc |
| EVAL-04 | Equality semantics cho `list` / `table` | Sai order/structure checking | Cần source gốc |
| EVAL-05 | Chính xác cách chấm `unanswerable`, `reason` và `answer:null` | Nhầm correct/unanswerable với abstain | Cần source gốc |

**Engineering policy:** Cho phép xây Pydantic schemas, validator và evaluator interface trước, nhưng test chính thức từng case phải dựa trên source đầy đủ, không đoán.

## 6. Test cases đã xác định được từ text

- Missing answer → abstain.
- Explicit `type: "abstain"` → abstain.
- Predicted type khác reference type (ngoài abstain) → wrong.
- Wrong substantive answer cho unanswerable reference → wrong.
- Reference tie tại boundary không xuất hiện trong test set theo mô tả; vẫn kiểm tra tie-break khi query.
- N questions equal weight.
