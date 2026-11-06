WHMX TOOL - LẤY DỮ LIỆU 物华弥新 TỪ MUMU
==========================================

Tool đọc thẳng dữ liệu game trong giả lập MuMu qua adb rồi xuất ra:
ảnh, audio (mp3), video (mp4), spine nhân vật, toàn bộ cốt truyện (txt),
ảnh CG theo truyện, 冬谷归档 (Đông Cốc quy đáng), hoán chương (焕彰), và phần thay đổi của mỗi bản cập nhật.
Tool chỉ ĐỌC dữ liệu trong máy ảo, không đăng nhập, không gửi gì lên server game.


1. CÀI ĐẶT (làm một lần)
------------------------
a) Cài Python 3.10 trở lên từ python.org.
   Khi cài nhớ tick "Add Python to PATH".

b) Mở cửa sổ lệnh (cmd) ngay trong thư mục tool này
   (gõ cmd vào thanh địa chỉ của File Explorer rồi Enter), chạy:
       pip install -r requirements.txt

c) MuMu Player (bản 12 hoặc Global 15 đều được):
   - Cài game 物华弥新 (package com.cipaishe.laigu) vào MuMu.
   - Mở game MỘT LẦN, để game tải xong toàn bộ dữ liệu (khoảng 10 GB), rồi TẮT game.
   - Trong cài đặt MuMu: bật Root và bật ADB (mục Khác / Other).
     Bật xong thì khởi động lại MuMu.

d) Kiểm tra:
       python whmx.py doctor
   Mọi dòng phải là [OK]. Dòng nào là [LỖI] thì làm theo hướng dẫn ghi trên dòng đó.


2. CÁC LỆNH
-----------
Mọi lệnh đều chạy trong thư mục tool:  python whmx.py <lệnh>
Kết quả nằm trong thư mục Xuat\ (đổi chỗ khác bằng --out "D:\ThuMucKhac").
MuMu phải đang mở. Game nên TẮT trong lúc chạy.

  doctor     Kiểm tra Python, thư viện, adb, root, game, dung lượng ổ đĩa.
  sync       Lưu dữ liệu của bản game hiện tại vào PhienBan\<bản>.
             Mọi lệnh khác đều tự chạy sync trước, nên thường không cần gọi riêng.
  images     Toàn bộ ảnh (khoảng 13 GB).              → Xuat\Anh\
               --nhanvat          chỉ ảnh nhân vật (tranh, skin, avatar, chibi...)
               --only CHUOI       chỉ bundle có tên chứa CHUOI, ví dụ:
                                  --only images_itemicons   (icon vật phẩm)
                                  --only character_drawing  (tranh lập thể)
  audio      Toàn bộ âm thanh ra mp3.                 → Xuat\Audio\
               --only CHUOI       ví dụ --only audio_a0001
  video      37 video, đã sửa để mở được.             → Xuat\Video\
  spine      Spine nhân vật (.skel, .atlas, .png).    → Xuat\Spine\
               --only a0142       chỉ một nhân vật
  lore       Toàn bộ chữ cốt truyện, phòng trà, thoại trong trận (tiếng Trung gốc).
                                                      → Xuat\Lore_<bản>\
  cg         Ảnh CG, xếp theo truyện dùng nó.         → Xuat\CG_TheoTruyen\
  dongcoc    冬谷归档: tạp chí, hồ sơ, hồi tưởng sự kiện, ngoại cần kiến văn, ảnh kỷ niệm.
             Một file html mở bằng trình duyệt (có sẵn ảnh) + thư mục images\ + raw\.
                                                      → Xuat\DongCoc_QuyDang\
  hoanchuong Mọi hoán chương (焕彰), mỗi cái một file txt: truyện, hiệu ứng kèm giải thích trạng thái,
             chỉ số từng cấp, nguyên liệu, ngày mở/đóng, màn thử.  → Xuat\HoanChuong_<bản>\
  update     So bản game hiện tại với bản trước: chỉ xuất phần MỚI và phần ĐÃ SỬA.
                                                      → Xuat\CapNhat_<cũ>_den_<mới>\
               --tu r3164         so từ một bản cụ thể trong PhienBan\
  all        Chạy lần lượt: images, audio, video, spine, lore, cg, dongcoc, hoanchuong.

Tuỳ chọn chung (đặt TRƯỚC tên lệnh):
  --out "D:\ThuMuc"   thư mục xuất
  --workers 4         số tiến trình xuất song song (máy yếu thì để 2)
  --adb "...\adb.exe" khi tool không tự tìm thấy adb
  --device ...        khi có nhiều máy ảo (xem bằng: adb devices)
Ví dụ:  python whmx.py --out "E:\WHMX" images --nhanvat

images và audio chạy tiếp được: đang chạy mà tắt thì chạy lại, tool bỏ qua những bundle
đã xong (ghi trong _xong_anh.txt / _xong_audio.txt). Lỗi ghi vào _loi.txt.


3. LẤY BẢN CẬP NHẬT MỚI
-----------------------
  1. Ngay lần đầu, chạy images và audio (hoặc all) để tool lưu bản hiện tại và nhớ từng ảnh/âm thanh.
     Chỉ chạy sync thì update vẫn chạy được, nhưng với bundle đã sửa tool chỉ biết ảnh nào có
     đường dẫn mới; ảnh bị vẽ lại mà giữ tên cũ sẽ không được xuất.
  2. Khi game ra update: mở game, đợi tải xong, TẮT game.
  3. Chạy:  python whmx.py update
Thư mục CapNhat_<cũ>_den_<mới>\ gồm:
  TOM_TAT.txt            tóm tắt: số bundle mới/sửa, nhân vật mới, bảng dữ liệu nào đổi
  DanhSach_bundle.txt    tên các bundle mới / đã sửa / video mới
  Anh\, Audio\, Video\   chỉ ảnh, audio, video MỚI hoặc ĐÃ ĐỔI (bundle đã sửa: ảnh không đổi bị bỏ qua)
  Text_thaydoi.txt       mọi dòng chữ mới hoặc đã sửa (tiểu sử, skin, vật phẩm, sự kiện...)
  MasterData_thaydoi\    mỗi bảng có thay đổi một file json:
                           "them" = dòng mới, "sua" = trường đã đổi [cũ, mới], "xoa" = dòng bị xoá
  Lore_thaydoi\          file cốt truyện mới hoặc đã sửa (cùng định dạng với lệnh lore)
  HoanChuong_thaydoi\    hoán chương mới hoặc đã sửa (cả khi chỉ đổi ngày mở/đóng)
Muốn so được thì phải có bản cũ trong PhienBan\, tức là phải chạy tool TRƯỚC khi game cập nhật.
Mỗi bản tốn khoảng 170 MB trong PhienBan\. Đừng xoá thư mục này.


4. ĐỊNH DẠNG LORE (Xuat\Lore_<bản>\)
-----------------------------------
  Main_Story\m001..     cốt truyện chính, mỗi chương một khu vực
  Events\e001..         cốt truyện sự kiện
  Side_Stories_B\       truyện nhánh
  Side_Stories_C\       truyện riêng của từng nhân vật
  Other_Stories\        truyện theo khu vực, hướng dẫn tân thủ, chế độ mùa
  LAN_Only\             có chữ nhưng không có dàn cảnh, chỉ liệt kê theo khoá
  Hightea\              phòng trà của từng nhân vật: trà thích, lời bình, kết, hỏi–đáp
  Battle_Dialogue\      thoại trong trận, theo mã màn
  INDEX.tsv             danh sách mọi kịch bản (mở bằng Excel)

Đầu mỗi file: TITLE_CN (tên hồi), EVENT_CN (tên sự kiện), VERSION, CHAPTER_CN, BRANCH_CN,
STORY_CN, REGION_CN, AREA_CN, PLOT_INFO_CN, SUMMARY_CN, CHARACTERS (nhân vật xuất hiện).
Trong thân file:
  旁白：                      lời dẫn truyện
  玩家 [player]：             người chơi
  名字 (tên Việt) [A0184]：   nhân vật chơi được (tên Việt lấy từ ten_nhan_vat.json)
  ？？？ (tên Việt) [A0184]：  game giấu tên nhưng biết là ai
  【字幕】                    phụ đề cảnh
  〔背景 bg_xxx〕             đổi nền / CG (khớp tên ảnh trong lệnh cg)
  ▸ 选项：                    lựa chọn của người chơi
Cuối file, mục 未引用 / 内部记录 là các dòng chữ không cảnh nào dùng (thường là ghi chú dàn dựng).


5. GẶP LỖI
----------
  "adb không thấy giả lập"      mở MuMu, bật ADB trong cài đặt, chạy lại doctor.
  "chưa có quyền root"          bật Root trong cài đặt MuMu, khởi động lại MuMu.
  "game đang tải update"        đợi game tải xong (thư mục temps trống) rồi chạy lại.
  "không khớp index"            game chưa cập nhật xong. Mở game cho tải hết, tắt game, chạy lại.
  "không giải được bundle"      game đổi kiểu mã hoá: cần sửa tool.
  _loi.txt có "PPtr can't deref"  bình thường, không mất ảnh: sprite đó nằm trong bundle atlas
                                khác và được xuất từ bundle atlas đó.
  Thư mục nhân vật chỉ có mã (ví dụ S0186): nhân vật mới chưa có tên Việt trong
  ten_nhan_vat.json. Thêm dòng  "S0186": "Tên Việt"  vào file đó (giữ đúng dấu phẩy, ngoặc kép).

Dung lượng tham khảo: images khoảng 13 GB, audio 4 GB, video 1 GB, cg 0.9 GB, dongcoc 75 MB,
lore 60 MB. Lệnh all cần ổ đĩa trống khoảng 20 GB.
