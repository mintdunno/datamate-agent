# Submission Guidance — Deadlines, Artifacts & Runtime

> **Source:** Tab **Submission Guidance** và các hướng dẫn Docker/Submission trong **Data** và **Rule** của [Zalo DataMate](https://challenge.zalo.ai/portal/datamate/) do người dùng cung cấp (2026-11-04).  
> **Cảnh báo nguồn:** Mốc release private test giữa Submission Guidance và homepage **không khớp**; xem mục 5.

## 1. Round 1 — Public Test

**Khoảng thời gian:** 2026-10-26 đến 2026-11-16.

- Teams chạy solution trên public test set và upload JSONL qua phần Submission trên portal.
- Tối đa **5 submissions/ngày**, quota reset cuối ngày.
- Public leaderboard dùng để tham khảo trong development.

### Final solution submission — 2026-11-16

- Hạn: **23:59 (GMT+7), 2026-11-16**.
- Phải nộp **complete solution**, bao gồm **training code, inference code và data/artifacts liên quan**, dưới dạng **Docker package/image**, theo hướng dẫn Docker riêng của ban tổ chức.
- Vì Docker package thường lớn, Submission Guidance yêu cầu cung cấp **checksum** và **public Google Drive link** để ban tổ chức tải về, qua form “Solution Submission”.
- Mỗi team tối đa **2 solution submissions**; ban tổ chức chỉ đánh giá **lần nộp cuối cùng**.

**Lưu ý:** “data” ở đây không có nghĩa được bundle private test data; `/data` do ban tổ chức mount khi run.

## 2. Round 2 — Private Test

Theo **Submission Guidance user-provided**:

- Release private test: **09:00 (GMT+7), 2026-11-20**.
- Team chạy **frozen solution** đã nộp trên private dataset và upload `submission.jsonl`.
- Hạn upload final result: **23:59 (GMT+7), 2026-11-23**.
- Private leaderboard hiển thị theo **chronological submission time**.
- Ban tổ chức kiểm tra source code, đối chiếu kết quả và phát hiện gian lận.
- **Top 2 teams** công bố **2026-12-02** (theo tab này).

**Quan trọng:** Final ranking lấy từ organizers' Docker re-run, không lấy uploaded file làm official final score.

## 3. Submission JSONL

**Artifact:** `submission.jsonl`, mỗi line là một JSON object độc lập.

Supported type examples:

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

- `reason` là optional.
- `alias` thuộc ground truth training, **không cần có** trong submission.
- Missing IDs scored as **abstain**.
- File phải được ghi vào **`/result/submission.jsonl`** khi ban tổ chức re-run.

## 4. Docker deliverables

Docker image/package cần chứa:

1. Training source code và inference source code.
2. Model weights phục vụ inference offline.
3. `predict.sh` làm entrypoint/chạy dự đoán theo hướng dẫn.
4. README hướng dẫn chạy và khai báo models.
5. Các phụ thuộc cần thiết để chạy với networking disabled.

Khi organizers mount test vào `/data`:

- `predict.sh` đọc dữ liệu từ `/data` (**read-only**).
- Ghi `/result/submission.jsonl`.
- Ghi **per-question logs** dưới `/result/logs/`.
- Logs tối thiểu có **question ID, final answer, elapsed time**; có thể bổ sung SQL/code, execution output và evidence để hỗ trợ điều tra reproducibility.
- `6 hours` wall-clock limit **bao gồm model loading**.
- Nếu quá giờ/dừng sớm, các câu chưa được ghi được xem là abstain.

**Không được** phụ thuộc vào API cloud tại final. Separate Docker guideline có thể đưa ra các yêu cầu cụ thể khác, cần đối chiếu trước khi build final image.

## 5. Chỗ mâu thuẫn cần xác nhận

| Event | Submission Guidance (copy do người dùng cung cấp) | Competition homepage (web) |
|---|---|---|
| Release private test, 2026-11-20 | **09:00 GMT+7** | **11:00 GMT+7** |

Homepage: https://challenge.zalo.ai/

**Không chọn một mốc làm fact cuối cùng** khi hai nguồn official đang mâu thuẫn. Đối chiếu lại live portal/thông báo organizer trước khi lên lịch.

## 6. Checklist trước khi nộp

- [ ] Models và licenses hợp lệ trước cutoff 2026-10-01.
- [ ] Total parameters across **all** inference models ≤15B (MoE tính toàn bộ).
- [ ] Docker image chạy offline, networking disabled.
- [ ] `predict.sh` đọc `/data`, ghi `/result/submission.jsonl`.
- [ ] `/data` không bị modify.
- [ ] Per-question logs tại `/result/logs/`.
- [ ] Process trong 6 giờ kể cả model loading.
- [ ] Cấu trúc JSONL và Answer Types đúng official spec.
- [ ] Không hardcode answer hoặc logic keyed IDs.
- [ ] Re-run ổn định đủ đáp ứng `|ΔPA| ≤ 0.03`.
- [ ] Đã kiểm tra separate Docker guideline.
- [ ] Đã xác minh giờ release private test nếu cần lập kế hoạch.
