# CATALOG LỖI DỮ LIỆU DATAMATE — TOÀN BỘ CÁC LOẠI ĐÃ XỬ LÝ

*Tổng hợp từ 16 business đã giải thật (dev_001–dev_014 public test + biz_106/biz_120 bộ train Hello, 1.237 câu). Mục đánh dấu [thật] = đã gặp và xử lý có số liệu; [phòng ngừa] = chưa gặp trong 16 business nhưng đã có luật trong đặc tả. Chi tiết luật xử lý: xem NORMALIZATION_SPEC.md (nhóm A–K, A10) và CHI_TIET_khau_bien_du_lieu_tho_thanh_sach.md.*

**Revision 4 (2026-11-05):** Polars/pure Python xử lý typed record batches; DuckDB cho storage/query/spill. Không thêm Pandas. Preparation không đọc evaluator aliases/answers. Số liệu lịch sử là tuyên bố bên ngoài chưa được repository kiểm chứng; không dùng làm target tuning. Unknown semantics chặn scope phụ thuộc; chỉ reuse facts qua rule changes khi chứng minh độc lập. Khả năng mở rộng qua khai báo không bảo đảm mọi ngành mới không cần typed extension/schema hoặc executor mới.

## 1. Lỗi cấu trúc file / sheet
1. [thật] Header không ở dòng 1: dòng 3 (biz_106), dòng 4 (dev_001/002/005), dòng 3 ở sheet bán dev_005 — phía trên là tiêu đề shop/báo cáo.
2. [thật] Header lặp giữa file: 226 dòng (dev_010), 420 dòng (dev_002).
3. [thật] Dòng trống và dòng tiêu đề trang trí xen giữa dữ liệu.
4. [thật] Một bảng bị xé thành nhiều file/sheet tháng: 17 file (dev_002), 18 sheet (biz_120), 15 sheet (dev_014), 18 file tháng (dev_013).
5. [thật] Dòng tổng ngày/tháng lẫn trong dòng bán: Tổng ngày/Cộng ngày/TỔNG NGÀY/TỔNG THÁNG — 587 (dev_008), 554 (dev_001), 540 (dev_009), 616 (dev_007), 511 (dev_010). Không lọc → SUM gấp đôi, MAX vớ phải dòng tổng.
6. [thật] Footer tổng chốt cuối sheet tháng: 15 footer (dev_014) — được giữ làm mỏ neo đối chiếu, khớp 15/15.
7. [thật] Dòng tiếp nối để trống trường cấp đơn (mã đơn/ngày/trạng thái): dev_005 — xử lý bằng forward-fill có kiểm soát trong cùng khối đơn.
8. [thật] Nguồn khác định dạng: Excel (.xlsx), CSV (encoding/delimiter phải dò), SQLite (dev_006).
9. [phòng ngừa] Ô gộp (merged cells) trong vùng dữ liệu — đặc tả A10: tách gộp tại staging, điền chỉ trong phạm vi vùng gộp và chỉ cột cấp-nhóm.
10. [thật] Thiếu hẳn một bảng/loại thông tin: biz_106 không có dữ liệu lô/hạn dùng, dev_004 không có cột nhóm sản phẩm.

## 2. Lỗi trùng lặp
11. [thật] Dòng trùng y hệt (exact duplicate) — quy mô dao động cực mạnh theo shop: 3.230 dòng (biz_106), 1.994 (dev_003), 1.381 (dev_007), 1.350 dòng chi tiết (dev_006), 1.347 (dev_008), 308 đơn (dev_009), 175 (dev_014), 166 (dev_013), và 0 (biz_120, dev_002, SoSuaXe dev_012). Chỉ khử trùng nếu cùng đơn và giống mọi ô; bảng không ghi theo đơn giữ dòng giống nhau (rulebook §8).
12. [thật] Trùng gần giống (khác nhau một trường nhỏ) — phải GIỮ, xoá là sai dữ liệu.

## 3. Lỗi định dạng giá trị
13. [thật] Tiền ghi dạng text, ít nhất 5 kiểu: `532.000đ`, `4,544tr`, `294,7703tr` (thập phân phẩy + hậu tố tr), `8tr399`, `16 076` (khoảng trắng phân cách nghìn).
14. [thật] Ngày nhiều định dạng trong cùng một cột/bảng: dd/mm/yyyy, datetime đầy đủ, chữ `9 thg 9 2025`, và số serial Excel trộn lẫn (dev_007).
15. [thật] Mã định danh dính khoảng trắng thừa → join không khớp cho đến khi strip (dev_010: 3.204 dòng thanh toán).
16. [thật] Số lượng ÂM mang nghĩa trả hàng: ở dev_004, đơn có trạng thái trả ⇔ có dòng số lượng âm, khớp tuyệt đối 95 đơn.
17. [thật] Một ô chứa hai trường: ô 2 IMEI `IMEI1 / IMEI2` (dev_014 — phải tách mới khớp bảo hành được), tên khách dính số điện thoại (biz_120), dòng topping gộp nhiều món trong một ô (dev_002 — tách theo README).

## 4. Lỗi danh tính thực thể
18. [thật] Cùng một món/khách, rất nhiều cách viết tên: ~100 biến thể quanh một món (dev_008), 440 cách viết → 50 mặt hàng (dev_002), 74→60 sản phẩm và 105→80 (dev_001/005 sau gộp). Mã/barcode có scope là bằng chứng mạnh; tên chuẩn hoá chỉ tạo ứng viên, cần kiểm tra cardinality/collision.
19. [thật] Đồng nghĩa địa phương — tên trong câu hỏi khác hẳn tên trong file: Má lợn = Má heo, Đậu phụ rán giòn = Đậu hũ chiên giòn, Canh chua cá tràu = cá lóc, Cháo ngao = nghêu, Mực rán = chiên, Lạc rang muối = Đậu phộng rang muối, Cát xây trát = Cát xây tô, Găng tay tất chân = Bao tay bao chân, Hoa quả nghiền = Trái cây nghiền, Cốc sạc = Củ sạc nhanh 20W, Kệ = Giá đỡ. Giá/ngày đổi khớp chỉ tạo ứng viên; cần khoá/khai báo hoặc bằng chứng độc lập và kiểm tra scope/cardinality/collision.
20. [thật] Khách vãng lai nhiều biến thể không có mã: lẻ / khách lẻ / kl — phải loại khỏi đếm và xếp hạng khách.
21. [thật] Cùng thực thể, hai tên gọi, nối bằng khoá chung: dòng xe ở dev_012 nối bằng biển số (AB = Air Blade, Alpha = Wave Alpha kể cả “Wave α”, Sirius FI = Sirius, Ex = Exciter…) bằng union-find.
22. [thật] Người được ghi bằng tên gọi tắt/biến thể: thợ dev_012 ghi “A.Đức / a Phúc / Đạt…” — token tên cuối tạo ứng viên; chỉ map hoa hồng khi scope/cardinality/collision phân biệt được bằng chứng.

## 5. Lỗi trạng thái và phân loại
23. [thật] Trạng thái đơn viết nhiều kiểu cho cùng một trạng thái chuẩn: đã xong / ok / Đã giao = completed; Không giao được = huỷ; đang ship / Đang vận chuyển / Đang xử lý = delivering (vẫn tính doanh thu); hàng trả về / Đã trả hàng = returned.
24. [thật] Trạng thái lạ ngoài từ điển hạt giống — phải qua điểm duyệt, map bừa là sai mẫu số tỉ lệ huỷ.
25. [thật] Phương thức thanh toán/kênh bán có biến thể viết — chuẩn hoá về từ vựng đóng của rulebook.

## 6. Lỗi đơn vị và quy đổi
26. [thật] Quy đổi chuỗi nhiều tầng: hộp → vỉ → viên (dev_005, duyệt đồ thị DanhSachQuyDoi không còn đơn vị nào chưa phân giải).
27. [thật] Quy đổi chỉ áp cho một số mã nhất định: hộp ×6 chỉ SP0049/SP0050 (dev_003) — áp cho cả bán và nhập; giá vốn phải tính theo đơn vị bán (90.000đ/hộp, không phải theo cái).
28. [thật] Quy đổi khai báo trong README: bó Ống PVC 1 bó = 10 cây (dev_004); tỉ lệ thùng/lốc khớp bảng README (dev_010).
29. [thật] Cột “số lượng đã quy đổi” tồn tại song song cột thô (dev_001) — phải đối chiếu hai đường quy đổi với nhau.

## 7. Lỗi ngữ nghĩa tiền và công nợ
30. [thật] Hai căn cứ tiền khác nhau trong cùng một shop: “Tổng sau giảm giá” của biz_106 gồm +15.000đ ship × 7.947 đơn — ship bị loại khỏi doanh thu nhưng tính vào công nợ (README + §4).
31. [thật] Phí phục vụ trà trộn vào danh mục mặt hàng (SP0061): tính vào doanh thu nhưng phải loại khỏi đếm/xếp hạng mặt hàng.
32. [thật] Công nợ giấu trong text tự do ở cột Note (dev_008): bóc bằng mẫu câu → 15 đơn còn nợ, tổng 4.549.169đ tại cuối dữ liệu.
33. [thật] Sổ quỹ chỉ ghi thu, không ghi chi (biz_106) — không được suy ra chi phí từ sổ này.
34. [thật] Phiếu nhập “đầu kỳ” trà trộn phiếu nhập thường: PN00001 trị giá 211.648.300đ (biz_120) — vẫn tính vào nhập tháng theo §5 (đáp án chuẩn xác nhận).
35. [thật] Cột “Khách trả” là giá trị suy biến: = Σ Thành tiền − Bớt − Thu máy cũ, khớp 100% trên 3.819 đơn (dev_011) và 6.334 đơn (dev_014).

## 8. Lỗi thời gian và kỳ tính
36. [thật] Tháng/quý câu hỏi không ghi năm → theo as_of_date: năm hiện tại nếu kỳ không sau hôm nay, ngược lại năm trước (§2); ngày NGUỒN thiếu năm dùng năm sheet (§8): “tháng 6”, “tháng ba”, th4–6…
37. [thật] Kỳ hỏi nằm hoàn toàn ngoài khoảng dữ liệu: 4 câu tháng 1/2025 của biz_106 (dữ liệu bắt đầu 01/02/2025) → unanswerable, KHÔNG phải 0 (cùng câu hỏi ở biz_120 lại trả lời được vì dữ liệu từ 01/01/2025).
38. [thật] Dữ liệu bán chỉ có ngày, không có giờ → mọi câu hỏi theo giờ/ca từ dữ liệu bán là không trả lời được (trừ khi có cột giờ tạo đơn như dev_003).

## 9. Lỗi “khái niệm không tồn tại trong dữ liệu” (nguồn của unanswerable)
39. [thật] Thuộc tính chủ quan không hề được ghi: khách thân thiện/trung thực nhất, nhân viên thân thiện/lành nghề nhất, sản phẩm ngon nhất/chất lượng tốt nhất, lý do trả hàng (lỗi món vs gọi nhầm), lý do huỷ đơn — tổng 26/1.000 câu public test và 7/237 câu Hello.
40. [thật] Thuộc tính khách không được ghi: giới tính (khách nữ), địa chỉ theo tên đường, kênh bán tại quầy/điện thoại, nhóm khách “thân thiết” ở shop không có dữ liệu nhóm, hàng sắp hết hạn ở shop không có dữ liệu lô/hạn dùng.

## 10. Lỗi đặc thù theo ngành (rulebook §6)
41. [thật] Nhà thuốc: trả hàng ghi ở bảng lô riêng — 354 đơn trả trong DonHang_Lo (dev_005) khớp tuyệt đối với trạng thái trả ở bảng bán; hoạt chất phối hợp là một loại riêng; tồn kho phải đối chiếu sổ thẻ kho (dev_006: khớp cả 80 sản phẩm).
42. [thật] Điện thoại: hợp đồng trả góp phải thoả Giá − Trả trước = Số kỳ × Góp/tháng (khớp 235/235 ở dev_014); bảo hành khớp theo từng IMEI sau khi tách ô 2 IMEI.
43. [thật] Sửa xe: phiếu bảo hành có Tiền = 0 (164 phiếu, dev_012) — giữ làm fact; việc tính vào lượt sửa phụ thuộc predicate measure theo nguồn/câu hỏi, không loại toàn cục; token công việc phải đếm theo từ điển (“sửa điện” không gồm thay bóng đèn/ắc quy; “ruột” ghi trơn là thay ruột, không phải vá).
44. [thật] VLXD: giá theo bảng giá có ngày hiệu lực (Cát xây tô 315.000đ từ 22/06/2025); một đơn có nhiều đợt giao — đếm theo cặp (Số đơn, Lượt giao).
