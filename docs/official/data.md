# Data — Dataset Specification

> **Source:** Tab **Data** của [Zalo DataMate](https://challenge.zalo.ai/portal/datamate/) do người dùng cung cấp nguyên nội dung text (2026-11-04).  
> **Coverage:** Nội dung text đã nhận; **hình minh họa Splits không có**.

## 1. Business types và quy mô

Dataset bao trùm nhiều loại small businesses ở Việt Nam: tạp hóa, quán cà phê/trà sữa, nhà hàng, nhà thuốc, cửa hàng vật liệu xây dựng, sửa xe máy, mẹ và bé, v.v.

- **Private test có business types không xuất hiện trong train hoặc public test**.
- Mỗi doanh nghiệp có **12–30 tháng** hoạt động, có seasonality như Tết, Trung thu, ngày lễ, ngày lĩnh lương và cuối tuần.
- Quy mô từ **vài trăm đến khoảng 300.000 dòng** dữ liệu mỗi doanh nghiệp.
- Chất lượng `README.md` rất không đồng đều: từ data dictionary chi tiết đến vài ghi chú không chính thức hoặc gần như không có thông tin.

## 2. Package structure

Mỗi split có layout logic sau (tên split cụ thể xác định theo dataset thực tế):

```text
<split>/
├── data/
│   └── <business_id>/
│       ├── *.csv | *.xlsx | *.sqlite   # 1 hoặc nhiều source files
│       └── README.md                  # business description / notes
├── rulebook.md                        # default rules & vocabulary CỦA SPLIT
├── calendar.csv                        # solar–lunar calendar
├── questions.jsonl                     # 1 question / line
└── answers.jsonl                       # chỉ trong training split
```

**Không suy ra rằng** có `rulebook.md` riêng trong mỗi business folder. File được mô tả ở **split root**.

## 3. Question schema

Ví dụ được cung cấp trong Data tab:

```json
{"id":"q_01234","business_id":"biz_017","question":"thang nay hang nao ban cham nhat, ton nhieu ma ban ko dc","as_of_date":"2026-08-27"}
```

Các field được xác nhận bằng ví dụ: `id`, `business_id`, `question`, `as_of_date`. Implementation cần validate dataset thật để biết liệu có optional fields hay không, không tự cấm field chưa được chứng minh là bất hợp lệ.

- `id`: dùng liên kết question và answer.
- `business_id`: tra business package tại `data/<business_id>/`.
- `question`: natural-language Vietnamese query.
- `as_of_date`: thời điểm tham chiếu để resolve relative dates.

## 4. Ground truth và `alias`

Training split đi kèm `answers.jsonl` chứa reference cho từng training question, liên kết bởi `id`.

Ví dụ:

```json
{"id":"q_06de33fa21cc","type":"string","answer":"Xi măng Hà Tiên PCB40 bao 50kg","alias":["SP0001"]}
```

- `alias` mô tả các tên/cách viết khác được chấp nhận cho answer dạng tên, bao gồm item code. **Submission không cần có `alias`.**
- Những câu unanswerable trong reference có **`answer: null`** (theo Data tab).
- Chưa có chi tiết đầy đủ về reference-matching logic theo type trong bản Evaluation text đã nhận: **không tự viết giả định** rằng việc xuất bất kỳ alias nào đều được xử lý theo cách cụ thể khi chưa xác minh.
- Ground truth **chỉ dùng cho Evaluator**. Nó không được accessible qua Agent SQL Tools, prompt, runtime catalog hoặc normalization.

## 5. Split-specific rulebook

**Mỗi split cung cấp `rulebook.md` riêng, và chúng có thể khác nhau.** Runtime phải luôn đọc rulebook đúng của split đang xử lý; không cache áp dụng luật train cho public/private.

Rulebook xác lập cách hiểu mặc định khi câu hỏi không chỉ định khác. Những ví dụ từ Data tab:

| Concept | Default interpretation trong Data tab |
|---|---|
| Revenue | Tổng line amounts **sau line-level discounts**; **không trừ order-level vouchers**; **không tính VAT/delivery fees**; loại **cancelled orders** và các đơn **có bất kỳ returned item** nào |
| “bán chạy” | Xếp theo **quantity** |
| Week | **Monday–Sunday** |
| “tháng này” | Tháng chứa `as_of_date`, tính **đến và bao gồm** ngày đó |
| Default cost | **Weighted-average purchase cost by lot** |
| Tết window | **23 tháng Chạp → mùng 10** (lunar calendar) |

Đây là **ví dụ typical rules**, không được xem là luật bất biến cho mọi split: lúc chạy phải đọc quy tắc trong split thực tế. Câu hỏi có tiêu chí cụ thể sẽ quyết định tiêu chí truy vấn.

## 6. `calendar.csv`

Cung cấp mapping solar–lunar. Không được giả định Tết/window chỉ bằng cách trừ cộng vài ngày Gregorian. Phải dùng calendar được cấp trong split, test các trường hợp qua năm và biên khoảng.

## 7. Submission types và examples

**Mỗi dòng** của `submission.jsonl` là một JSON object:

```jsonl
{"id":"q_01234","type":"string","answer":"Sting dâu 330ml"}
{"id":"q_01235","type":"number","answer":48250000}
{"id":"q_01236","type":"percent","answer":-12.45}
{"id":"q_01237","type":"date","answer":"2026-08-15"}
{"id":"q_01238","type":"boolean","answer":true}
{"id":"q_01239","type":"list","answer":["Cà phê sữa","Bạc xỉu","Trà đào"]}
{"id":"q_01240","type":"table","answer":{"columns":["khach_hang","so_tien_no"],"rows":[["Chị Lan",3200000],["Anh Tú",1500000]]}}
{"id":"q_01241","type":"unanswerable","reason":"Dữ liệu không có giá vốn nên không tính được lợi nhuận"}
{"id":"q_01242","type":"abstain"}
```

- `reason` optional (theo Data tab).
- `abstain` có thể chỉ cần `id` và `type`.
- Question không xuất hiện trong submission sẽ được tính **abstain**.
- `percent` trong ví dụ là `-12.45` — không được tự đổi về `-0.1245` nếu luật không nói vậy.

## 8. Docker/runtime contract trích từ Data tab

Final Docker image phải chứa source code (training và inference), model weights, `predict.sh` và README. Ban tổ chức:

- Mount test set vào `/data` (**read-only** theo Rule tab).
- Thu output tại `/result/submission.jsonl`.
- Yêu cầu per-question log tại `/result/logs/`, tối thiểu gồm question ID, final answer và elapsed time; nên log thêm executed code, query, tool execution để hỗ trợ discrepancy investigation (Rule 8).

**Lưu ý:** Separate Docker guideline còn cần đối chiếu để biết base image / packaging chi tiết.

## 9. Chưa xác minh

- **Splits diagram** được cung cấp dưới dạng ảnh, không nằm trong text đã nhận. Không ghi giả số lượng businesses/questions per split.
- Các chi tiết equality/tolerance của answer types còn thiếu ở bản Evaluation đã nhận.
