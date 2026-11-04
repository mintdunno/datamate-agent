# Rule — Competition Rounds & Technical Requirements

> **Source:** Tab **Rule** của [Zalo DataMate](https://challenge.zalo.ai/portal/datamate/) do người dùng sao chép (2026-11-04).  
> **Scope:** Quy tắc thi ở cấp solution. Khác với `rulebook.md` ở root của từng data split.

## 1. Competition rounds

| Giai đoạn | Mô tả | Giá trị của leaderboard |
|---|---|---|
| Round 1 — Public Test | Team phát triển solution, chạy public set, upload `submission.jsonl` | Điểm tham khảo để phát triển |
| Final Solution Submission | Nộp Docker image trước deadline, solution được freeze | Ban tổ chức lưu solution để re-run |
| Round 2 — Private Test | Sau khi freeze, team được cung cấp private set, chạy solution đã nộp và upload `submission.jsonl` | Provisional; final dựa trên organizers' re-run |

Ban tổ chức mount test set vào `/data` và thu `/result/submission.jsonl`.

## 2. Technical requirements

### R1 — Teams and eligibility

Tuân theo general regulations của Zalo AI Challenge 2026. Phần detailed regulations chưa được chép vào bộ tài liệu này.

### R2 — Models

- Chỉ sử dụng model có **weights công khai trước 2026-10-01** và license cho phép sử dụng theo yêu cầu thi.
- Được phép **fine-tune**.
- **Tổng parameters** của **tất cả** models được sử dụng tại inference (LLM, embedding, reranker, classifiers, …) **không được vượt 15B**.
- Tổng tính cho **mọi model từng được dùng**, **dù load đồng thời hay lần lượt**.
- Với **Mixture-of-Experts (MoE)**, tính **tất cả parameters**, không chỉ active parameters.
- Cho phép mọi phương pháp **quantization**, nhưng quantization không làm giảm parameter count để vượt giới hạn.
- Phải khai báo models trong solution README.

### R3 — No external services

Inference chạy **100% offline trong Docker image**. Không dùng commercial LLM API, tải xuống hay truy cập network. Ban tổ chức chạy image với **networking disabled**.

### R4 — No human in the loop

Không được cần can thiệp thủ công để trả lời. Cấm **hardcoded answers** và logic keyed bằng **question ID** hoặc **business ID**.

### R5 — Code execution

Agent **được phép** generate/execute code trong container; đây là hướng được dự kiến nhưng **không bắt buộc**. `/data` là read-only; temp files phải viết tại nơi khác.

### R6 — Data usage

Được sử dụng:

- Provided training data.
- Dữ liệu synthetic tự tạo.
- Publicly available datasets/models.

**Không được** dùng public/private test data cho training, hoặc thu thập answers cho test questions bằng bất kỳ cách nào.

### R7 — Compute reference và runtime limit

- **2 × NVIDIA RTX 5090**, mỗi GPU **32GB VRAM** (tổng **64GB**).
- **512GB RAM**.
- Phải xử lý toàn bộ private set trong **6 giờ wall-clock**, **kể cả model loading**.
- RTX 5090 / Blackwell yêu cầu **CUDA ≥ 12.8** và build PyTorch/vLLM tương thích; Docker guideline sẽ chỉ rõ base image.

### R8 — Final score và reproducibility

- **Final score** = kết quả **organizers' re-run** Docker image trên private set.
- File private `submission.jsonl` team upload trước chỉ quyết định provisional leaderboard và đội nào được re-run.
- Yêu cầu `|PA(uploaded) − PA(re-run)| ≤ 0.03`.
- Nếu quá 0.03, ban tổ chức xem logs, có thể re-run thêm một lần và yêu cầu thông tin bổ sung; nếu không giải thích được có thể bị disqualified.
- Nếu re-run vượt 6 giờ hoặc dừng sớm, **chỉ các đáp án đã ghi** vào `/result/submission.jsonl` trước khi dừng được chấm; phần còn lại tính abstain.

## 3. Implications cho Harness

- Tooling và LLM backend phải có **offline-mode**; NVIDIA NIM cloud **chỉ phù hợp cho development**, không đủ điều kiện final inference.
- Cần **model inventory** và **parameter budget** ở inference; không “đổi model lần lượt” để lách giới hạn.
- Phải có logging mỗi question, deterministic sorting/normalization, kiểm soát randomness, stable output và flush/progressive write.
- Không dựa vào question/business IDs để định tuyến logic đặc thù.
- Không được biến test questions thành dữ liệu train hoặc thu thập reference answers.
- Cần test sớm end-to-end với network disabled và đúng mount paths.

## 4. Chưa có trong bộ tài liệu

- General competition regulations / team eligibility details.
- Separate Docker guideline, base image, packaging constraints.
