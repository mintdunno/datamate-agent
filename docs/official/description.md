# Description — DataMate

> **Source:** [Zalo DataMate — Description](https://challenge.zalo.ai/portal/datamate/).  
> **Verification:** Nội dung Description đọc trực tiếp từ portal (2026-11-04); phần dưới diễn giải tiếng Việt theo cấu trúc dễ triển khai.

## 1. Bài toán

DataMate yêu cầu xây dựng **AI Agent trả lời câu hỏi tiếng Việt trên structured data**, thay cho người dùng không biết SQL. Dữ liệu nguồn có thể rời rạc, thiếu nhất quán, hoặc có business semantics riêng.

Ứng dụng được minh họa trong nhiều ngữ cảnh: tiệm bán lẻ, quán ăn/uống, giáo dục, y tế, nhân sự, hợp tác xã và các tổ chức làm việc với CSV, Excel, SQLite. Trọng tâm dữ liệu cuộc thi là doanh nghiệp nhỏ ở Việt Nam, nhưng Agent phải có khả năng xử lý dữ liệu bảng nói chung.

**Core task:** Cho data package và câu hỏi tiếng Việt, Agent phải tính ra **đáp án dựa trên dữ liệu**, hoặc chỉ ra không thể trả lời khi thông tin không đủ. Việc không chắc chắn cũng có thể dẫn đến `abstain` theo contract.

## 2. Input contract

| Input | Ý nghĩa |
|---|---|
| Business package | Một hoặc nhiều `.csv`, `.xlsx`, `.sqlite` và `README.md` mô tả doanh nghiệp |
| Question | Câu hỏi tiếng Việt tự nhiên, có thể viết tắt hoặc dùng ngôn ngữ đời thường |
| `as_of_date` | Mốc “hôm nay” dùng giải các biểu thức thời gian tương đối như “tháng này”, “tuần rồi”, “cùng kỳ năm ngoái” |
| Split `rulebook.md` | Quy tắc và vocabulary mặc định chung cho business trong split |
| `calendar.csv` | Solar–lunar calendar phục vụ logic ngày âm/lễ Tết |

**Quan trọng:** `as_of_date` là input của từng câu hỏi, không thay bằng system current date.

### Ví dụ question (theo Description)

```json
{"id":"q_06de33fa21cc","business_id":"biz_118","question":"sp bán dc nhiều nhất tuần rồi là gì","as_of_date":"2026-03-04"}
```

## 3. Output contract

Kết quả mỗi câu thuộc một trong các type:

`number`, `percent`, `string`, `date`, `boolean`, `list`, `table`, `unanswerable`, `abstain`.

### Ví dụ submission answer

```json
{"id":"q_06de33fa21cc","type":"string","answer":"Xi măng Hà Tiên PCB40 bao 50kg"}
```

**Các semantics chi tiết** về `type`, equality và output format xem `evaluation.md` và `data.md`; không tự suy diễn từ ví dụ đơn lẻ.

## 4. Yêu cầu về generalization

- Không hardcode tên doanh nghiệp, câu hỏi hoặc answer.
- Có thể gặp schema và business type chưa từng thấy trong train.
- Cần hiểu đúng business rules, thời gian, đơn vị và phép tính, thay vì chỉ dùng keyword search.
- Khi dữ liệu không hỗ trợ câu hỏi, có cách biểu diễn `unanswerable`; `abstain` là lựa chọn không đưa ra câu trả lời.

## 5. Source

- https://challenge.zalo.ai/portal/datamate/
