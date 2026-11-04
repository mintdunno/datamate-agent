# DataMate — Bộ tài liệu cuộc thi cho AI Coding Agent

> **Phạm vi:** Zalo AI Challenge 2026, **DataMate — Talking To Your Data Through Agentic AI**.  
> **Ngôn ngữ:** Tiếng Việt; giữ nguyên technical terms và names của format/API.  
> **Ngày tổng hợp:** 2026-11-04.  
> **Nguồn:** [Official DataMate Portal](https://challenge.zalo.ai/portal/datamate/) (Description truy xuất trực tiếp), các tab **Data, Evaluation, Rule, Submission Guidance** do người dùng sao chép từ portal. Đây là tài liệu **tái cấu trúc/diễn giải để triển khai**, không phải bản gốc có chữ ký của ban tổ chức.

## 1. Thứ tự đọc bắt buộc

| Thứ tự | File | Đọc để làm gì? |
|---|---|---|
| 1 | [`official/description.md`](official/description.md) | Bài toán, Input/Output, ví dụ |
| 2 | [`official/data.md`](official/data.md) | Split layout, `rulebook.md`, `calendar.csv`, labels và alias |
| 3 | [`official/evaluation.md`](official/evaluation.md) | Scoring, ranking, xử lý abstain, các điểm còn thiếu |
| 4 | [`official/rule.md`](official/rule.md) | Eligibility, model limits, offline, reproducibility |
| 5 | [`official/submission_guidance.md`](official/submission_guidance.md) | Deadlines, upload quotas, Docker deliverables, runtime paths |
| 6 | [`engineering/implementation_contract.md`](engineering/implementation_contract.md) | Chuyển quy định thành checklist và yêu cầu kiểm thử cho Harness v0.1 |

## 2. Quy tắc sử dụng tài liệu

- **Official source of truth** là nội dung trên portal, file `rulebook.md` thuộc **chính split đang xử lý**, và `README.md` của doanh nghiệp tương ứng. Nếu có thay đổi chính thức, tài liệu mới nhất của ban tổ chức được ưu tiên.
- Một số luật **không được suy diễn** từ ví dụ: score formula bị mất trong bản copy, và chi tiết value-matching theo từng type chưa xuất hiện. Xem mục **Open Questions** của `official/evaluation.md`.
- **Không được lấy rulebook của train áp dụng cho public/private**. Mỗi split chứa `rulebook.md` riêng, có thể khác nhau.
- **Question-level explicit criterion** được ưu tiên hơn default criterion trong rulebook khi câu hỏi nêu tiêu chí cụ thể.
- **Ground truth (`answers.jsonl`) chỉ ở train**. Phải tách biệt khỏi Agent Runtime; chỉ Evaluator được đọc.
- **Public/private test không được dùng để train** hoặc để thu thập answer labels; bảo đảm việc phát triển tuân thủ Rule 6.
- Các giới hạn sample khi AI Coding Agent khảo sát workspace **không** phải giới hạn số dòng mà Runtime được xử lý.

## 3. Những điểm quan trọng cần chú ý

1. **Scoring formula / matching rules chưa đầy đủ** do nội dung Evaluation bị mất đoạn khi copy (nhiều khả năng phần công thức/render). Không được viết scorer và khẳng định tương thích chính thức cho đến khi xác minh.
   **Stage 1 clarification:** train rulebook §§7, 9 đã cung cấp nhiều format,
   matching rules và outcome points. Xem [Evaluation foundation](engineering/evaluation_foundation.md)
   để phân biệt semantics đã có nguồn với các khoảng trống còn unresolved.
2. **Mâu thuẫn giờ release private test:** Submission Guidance do người dùng cung cấp nêu **09:00, 20/11/2026**; homepage Zalo nêu **11:00, 20/11/2026**. Giữ nguyên cả hai nguồn và xác nhận lại trên portal; không tùy tiện chọn một.
3. **Separate Docker guideline chưa có**. Các chi tiết như base image và lệnh build phải lấy từ guideline này, không tự nhận là yêu cầu official.
4. **Sơ đồ “Splits” trong Data tab là ảnh** và không được cung cấp; không suy diễn số lượng split/business/questions từ ảnh.

## 4. Tránh nhầm lẫn giữa document và file runtime

- `docs/official/*.md`: tài liệu biên soạn từ các tab của portal, **không tự động** là runtime rulebook.
- `<split>/rulebook.md`: **rulebook runtime thực tế**, phải parse đúng file của split đó.
- `<split>/data/<business_id>/README.md`: business description và notes, độ đầy đủ rất khác nhau.

## 5. Source links

- DataMate: https://challenge.zalo.ai/portal/datamate/
- Competition homepage: https://challenge.zalo.ai/
