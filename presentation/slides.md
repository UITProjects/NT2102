---
theme: default
title: Phát hiện bất thường trên endpoint device log dựa trên OCR-APT
info: |
  NT2102 – Đồ án phát hiện bất thường trên log thiết bị đầu cuối.
  Demo trực tiếp OCR-APT trên một Windows host (ws-etw-0, exp08).
colorSchema: light
aspectRatio: 16/9
canvasWidth: 980
fonts:
  sans: Be Vietnam Pro
  mono: JetBrains Mono
transition: slide-left | slide-right
drawings:
  persist: false
layout: cover
class: text-left
background: '#f6f8fd'
---

<div class="flex items-center gap-4 mb-6">
  <img src="./images/logo-uit.jpg" class="h-16" />
  <div class="text-sm leading-tight muted">
    ĐẠI HỌC QUỐC GIA TP.HCM<br/>TRƯỜNG ĐẠI HỌC CÔNG NGHỆ THÔNG TIN
  </div>
</div>

<h1 v-motion :initial="{ x: -60, opacity: 0 }" :enter="{ x: 0, opacity: 1, transition: { duration: 700 } }" style="font-size:2.05rem;line-height:1.2;color:#1e3a8a" class="mb-8">Phát hiện bất thường trên endpoint device log dựa trên OCR-APT</h1>

<div v-motion :initial="{ y: 30, opacity: 0 }" :enter="{ y: 0, opacity: 1, transition: { duration: 700, delay: 350 } }" class="grid grid-cols-2 gap-6 text-sm">
<div>

**Môn học:** An toàn bảo mật hệ thống thông tin<br/>
**GV:** Nguyễn Tấn Cầm<br/>
Học kì 2026–2027

</div>
<div>

**Thành viên**<br/>
Ngô Vũ Minh Đạt – 26848033@grad.uit.edu.vn<br/>
Nguyễn Long Vũ – 250202060@grad.uit.edu.vn<br/>
Trần Quang Kiệt – 26848108@grad.uit.edu.vn

</div>
</div>

<!--
Chào thầy/cô và các bạn. Nhóm trình bày một demo trực tiếp của OCR-APT (CCS 2025): phát hiện tấn công
trên log nhân (kernel log) của Windows bằng subgraph anomaly detection. Bốn phần: bài toán → ý tưởng cốt
lõi (5 bước) → demo trên một máy Windows → cảm ơn. Chi tiết đầy đủ nằm trong báo cáo (latex/).
-->

---
clicks: 2
---

# 1 · Bài toán & OCR-APT

<div class="cmp small" style="grid-template-columns: 1fr 1fr 1.25fr; gap:0.8rem">
<div class="card neutral">

**Vì sao khó**
- Audit log **khổng lồ**
- Tấn công **hiếm**, **không nhãn**

</div>
<div v-click="1" class="card atk">

**Cách cũ**
- Rule → **bỏ sót** kỹ thuật mới
- Node **vụn** · graph **thô** · không có **câu chuyện**

</div>
<div v-click="2" class="card det">

**OCR-APT** (CCS 2025)
- **Subgraph** · **behavior** · **LLM**
- Mục tiêu: **1 host**, tấn công **đỏ**, benign **xanh**, live

</div>
</div>

<div class="relative mt-3">
  <img src="./images/paper-fig1.png" class="fig" style="max-height:230px;object-fit:contain" />
  <span class="num absolute" style="left:8.5%;top:14%">1</span>
  <span class="num absolute" style="left:10.3%;top:60%">2</span>
  <span class="num absolute" style="left:31.2%;top:64%">3</span>
  <span class="num absolute" style="left:52.6%;top:57%">4</span>
  <span class="num absolute" style="left:79.6%;top:57%">5</span>
</div>
<div class="src">Hình 1 của Aly et al., CCS 2025; số thứ tự do nhóm thêm vào</div>

<div class="tiny mt-1">
<span class="num">1</span> provenance graph &nbsp;·&nbsp; <span class="num">2</span> behavior feature &nbsp;·&nbsp; <span class="num">3</span> OCRGCN theo loại node &nbsp;·&nbsp; <span class="num">4</span> anomalous subgraph &nbsp;·&nbsp; <span class="num">5</span> LLM báo cáo
</div>

<!--
Tấn công ẩn trong audit log khổng lồ, cực hiếm, không nhãn. Cách cũ: luật thì bỏ sót kỹ thuật mới;
anomaly detection thì hoặc quá vụn (từng node) hoặc quá thô (cả đồ thị); và chỉ cho cảnh báo rời, không
dựng được câu chuyện. OCR-APT trả lời bằng subgraph + học hành vi (không dùng tên/IP) + LLM.
Mục tiêu đồ án rất hẹp: demo phần lõi chạy sống trên một Windows host, không đua số liệu với bài báo.
Năm bước dưới hình là bản đồ cho phần 2. Chi tiết hạn chế cách cũ: note.txt Q8; báo cáo §1.
-->

---
clicks: 4
---

# 2 · ① Provenance graph

<AttackGraph />

<div class="cmp small">
<div>

- Node: `PROCESS` · `FILE` · `NET`
- Edge: `Start` `Load` `Read` `Write` `Send` `Recv` + thời gian

</div>
<div>

- Tên / path / IP → **không vào model**
- Giả định: log train **sạch**

</div>
</div>

<!--
Mỗi click thêm một bước tấn công theo thời gian. Process làm gì với file và mạng trở thành đồ thị; chủ
thể luôn là process. Loại edge quan trọng — "ghi" khác "đọc". Tên/IP được giữ lại cho bước điều tra
nhưng KHÔNG đưa vào mô hình, để mô hình không học bề mặt dễ giả mạo. Threat model: log huấn luyện sạch.
-->

---
clicks: 4
---

# 2 · ② Behavior feature: đếm hành động

<div class="small muted">Mỗi node: <b>làm gì, bao nhiêu lần</b> · 12 out/in + 3 idle = <b>15 số</b> · không tên/IP</div>

<BehaviorCounts />

<!--
Trực giác quan trọng nhất của đồ án. Click 1: chrome duyệt web. Click 2: một macro mở powershell.
Click 3: rundll32 nạp DLL rồi beacon ra ngoài. Bảng đếm dần ĐÚNG các feature đem huấn luyện: mỗi event
cộng 1 vào out_<action> của source và 1 vào in_<action> của destination. 12 cột đếm (chuẩn hoá L2) + 3 cột
thời gian nghỉ (min-max) = 15 số mỗi node → ma trận [9, 15] cho vết 17 event này. rundll32 im 214 s trước
khi gửi ra ngoài → max nghỉ 0.99. Bảng đầy đủ: báo cáo Bảng feature cuối (§2.2). Ví dụ số L2: note.txt Q-L2.
-->

---
clicks: 4
---

# 2 · ③ GNN: message passing

<div class="grid gap-6 mt-1" style="grid-template-columns: 0.6fr 1.6fr">
<div class="small">

- Node = **vectơ** $h$
- Cập nhật từ **chính nó + neighbor**
- **L lớp** = nhìn xa **L bước**

</div>
<div class="small">

<MessagePassing />

</div>
</div>

<!--
Chuyển sang mô hình. ML thường coi mỗi hàng độc lập; đồ thị thì mỗi node có số neighbor khác nhau. GNN
giải quyết bằng message passing: mỗi lớp, mọi node gộp thông tin từ chính nó và các neighbor rồi cập nhật
vectơ. Xếp L lớp thì nhìn xa L bước; vectơ sau mỗi lớp là hidden layer, lớp cuối là embedding.
OCR-APT dùng 3 lớp, exp08 dùng 2 lớp. Ví dụ số: note.txt Q5; vì sao không 10 lớp: Q6; báo cáo §2.3.
-->

---
clicks: 5
---

# 2 · ③ RGCN: một trọng số cho mỗi loại edge

<RgcnLayer />

<div class="small mt-2 text-center">

**$W_r$ riêng** mỗi loại edge · `Load` ≠ `Write` · 15 feature → **embedding 32 chiều**

</div>

<!--
RGCN = Relational GCN. Mỗi loại edge (Write/Read/Send/Recv/Start/Load) có ma trận trọng số riêng W_r, nên
mô hình học mỗi loại "kéo" bao nhiêu. Animation: rundll32 gộp chính nó + 2 file + 1 flow, mỗi loại qua
trọng số riêng → 0.1. Sau 2–3 lớp, 15 feature thô thành embedding 32 chiều mang cả chuỗi tài liệu →
powershell → DLL → rundll32 → mạng. Vì sao RGCN chứ không GCN: note.txt Q1; 12 quan hệ: Q3; công thức: báo cáo §2.3.
-->

---
clicks: 2
---

# 2 · ③ OCRGCN: học "bình thường", gắn cờ cái ở xa

<div class="grid gap-4" style="grid-template-columns: 1.15fr 1fr">
<div>
<Hypersphere />
</div>
<div class="small">

- Không nhãn → chỉ học **benign**
- Trong cầu = **quen** · ngoài cầu = **gắn cờ**
- $s_i = \lVert \mathbf z_i - \mathbf c \rVert^2 - R^2 > \theta$

<div v-click="2" class="card det mt-3">

**3 loại node → 3 quả cầu, 3 threshold**<br/>
process · file · mạng: "bình thường" khác nhau

</div>

</div>
</div>

<!--
Không có nhãn nên chỉ học "bình thường": nhốt benign embedding vào một quả cầu (tâm c, bán kính R), cái
gì ra ngoài thì nghi ngờ — tinh thần One-Class SVM. OCRGCN = encoder RGCN + loss kiểu Deep SVDD. sᵢ là
khoảng cách tới tâm trừ bán kính; vượt threshold θ thì gắn cờ. Điểm then chốt: một mô hình cho MỖI loại
node, vì "bình thường" của process/file/mạng khác nhau. Encoder vẫn nhìn toàn đồ thị; chỉ loss/threshold
theo loại. β, threshold, Hình 2 bài báo: báo cáo §2.4–2.5.
-->

---

# 2 · ④⑤ Subgraph → cảnh báo → LLM

<div class="cmp mt-4">
<div class="card det">

### ④ Alg. 1 + SG-Det
- Nối node bị cờ → **subgraph**
- Điểm = **Σ xác suất** node bị cờ
- **≥ 10 → Moderate → đỏ** (thang bài báo)

</div>
<div class="card inv">

### ⑤ LLM
- Subgraph → **IOC** → lọc hallucination → **báo cáo**
- [OURS] **triage** trước: `attack` / `benign` / `uncertain`
- **Overlay**: không xoá cảnh báo, không vào training

</div>
</div>

<!--
Node rời rạc → subgraph có điểm → báo cáo. Điểm subgraph là TỔNG xác suất (min-max, kẹp [0,1]) của node
bị cờ, nên ≥10 ≈ ít nhất ~10 node bất thường nối thành một câu chuyện. Thang Minor <10, Moderate 10–100,
Significant 100–1000, Critical >1000 (§7.2.1); không dùng top-k fallback vì sẽ làm mọi window đỏ.
LLM đọc subgraph, trích IOC, lọc phần bịa, viết báo cáo; nhóm thêm bước phân loại trước (bài báo luôn giả
định tấn công). LLM chỉ là lớp phủ (rủi ro prompt injection). Công thức: báo cáo Phụ lục.
-->

---

# 3 · Demo: hạ tầng lab (Proxmox)

<div style="max-width:84%;margin:0 auto"><LabInfra /></div>

<div class="tiny muted mt-1 text-center">Terraform + Ansible · <b>một</b> Windows host được chấm điểm</div>

<!--
Lab trên một hypervisor Proxmox. ws-etw-0 chạy DVWA (web có lỗ hổng) và etwcollect (collector ETW nhóm tự viết).
ubuntu-trainer chạy Loki, Grafana, huấn luyện và ocr-live (máy chủ demo). Kali ở VNet riêng là máy tấn
công. Ba mũi tên: ① Kali khai thác DVWA, ② log ETW đẩy về Loki, ③ ocr-live phục vụ giao diện cảnh báo.
Đồ án cam kết một host; host thứ hai + correlation là nice-to-have.
-->

---

# 3 · Đường đi dữ liệu

<OurPipeline />

<div class="flowstrip mt-3">
  <span>JSON line</span><b>→</b>
  <span><i class="num">1</i>edge (src, action, dst, ts)</span><b>→</b>
  <span><i class="num">2</i>X [N, 15]</span><b>→</b>
  <span><i class="num">3</i>sᵢ mỗi node</span><b>→</b>
  <span><i class="num">4</i>subgraph Σp ≥ 10</span><b>→</b>
  <span><i class="num">5</i>xanh / đỏ + báo cáo</span>
</div>

<div class="small muted mt-3 text-center">Benign = <b>idle 3 ngày</b> · <b>cùng code</b> train ↔ live</div>

<style scoped>
.flowstrip { display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 0.3rem; font-size: 0.74rem; flex-wrap: nowrap; }
.flowstrip span { display: inline-flex; align-items: center; gap: 0.3rem; padding: 0.2rem 0.55rem; border: 1px solid #d1d5db;
  border-radius: 6px; background: #f8fafc; white-space: nowrap; }
.flowstrip b { color: #6b7280; }
.flowstrip .num { font-style: normal; transform: scale(0.8); }
</style>

<!--
Theo một dòng log đi hết hệ thống. etwcollect trên ws-etw-0 đọc ETW (process/file/mạng), ghi thành dòng
JSON và tự đẩy về Loki trên ubuntu-trainer. Từ đó: ① dòng log thành edge (process nào làm gì với ai, lúc
nào), cắt theo window 5 phút; ② mỗi node thành 15 số, dùng scaler của tập train; ③ OCRGCN cho mỗi node một
điểm sᵢ; ④ Alg. 1 nối các node bị cờ thành subgraph, tổng xác suất ≥ 10 thì window đỏ; ⑤ LLM (tuỳ chọn)
phân loại và viết báo cáo; giao diện web hiện window xanh/đỏ. Cùng một code cho huấn luyện (benign idle
~3 ngày kéo từ Loki) và demo. Cách cắt window (sliding khi train, tumbling khi live): báo cáo §3.
-->

---
clicks: 8
---

# 3 · Kịch bản tấn công: RCE kill chain

<KillChain />

<div class="tiny muted mt-1">Kali → DVWA trên ws-etw-0 · nhãn tự ghi (self-log) làm oracle</div>

<!--
Một chuỗi tấn công duy nhất, chạy lại được, từ Kali. Click qua từng giai đoạn: khai thác DVWA command
injection → RCE (httpd→cmd→powershell) → discovery → quét mạng → tải & chạy tool thật (PsExec, rclone)
→ dump MySQL → C2 beacon → scheduled task → xoá log (T1070). Lab cô lập, Defender tắt, VM dùng một lần.
Script: src/atomic-attack-pack/scenarios/rce-killchain.ps1.
-->

---
layout: center
class: text-center
---

# 3 · Video demo

<div class="video-ph" v-motion :initial="{ scale: 0.95, opacity: 0 }" :enter="{ scale: 1, opacity: 1, transition: { duration: 500 } }">
  <iframe src="https://www.youtube-nocookie.com/embed/Jzv03v82bNo?rel=0"
    title="Video demo" frameborder="0" allowfullscreen
    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" />
</div>

<div class="tiny muted mt-3">① idle <b>xanh</b> → ② kill chain chạy → ③ window <b>đỏ</b> + subgraph → ④ kết luận LLM · <a href="https://www.youtube.com/watch?v=Jzv03v82bNo" target="_blank">youtu.be/Jzv03v82bNo</a></div>

<style scoped>
.video-ph { width: 76%; margin: 0.5rem auto 0; aspect-ratio: 16/9; border-radius: 14px; overflow: hidden;
  background: #0f172a; box-shadow: 0 6px 24px rgba(15,23,42,0.25); }
.video-ph iframe { width: 100%; height: 100%; display: block; }
</style>

<!--
Video demo đã quay sẵn (YouTube: https://www.youtube.com/watch?v=Jzv03v82bNo). Cần mạng để phát;
nếu mất mạng, mô tả bằng lời theo bốn bước dưới khung.
Kết quả đo được (exp08: window tấn công 70.17 / LLM attack 0.85; 24 h: 288 window, 7 alert, 3 false alarm)
và giới hạn nằm trong báo cáo §3–4; nếu được hỏi: note.txt Q7. Slide sau là lời cảm ơn.
-->

---
layout: center
class: text-center
transition: fade
---

<img src="./images/logo-uit.jpg" class="h-20 mx-auto mb-6" v-motion :initial="{ scale: 0.6, opacity: 0 }" :enter="{ scale: 1, opacity: 1, transition: { duration: 600 } }" />

<h1 v-motion :initial="{ y: 30, opacity: 0 }" :enter="{ y: 0, opacity: 1, transition: { duration: 700, delay: 200 } }" style="font-size:2.6rem;color:#1e3a8a">Cảm ơn thầy/cô và các bạn đã lắng nghe!</h1>

<div v-motion :initial="{ y: 30, opacity: 0 }" :enter="{ y: 0, opacity: 1, transition: { duration: 700, delay: 500 } }" class="text-xl mt-2">Hỏi & Đáp</div>

<div class="small muted mt-10">

Kết quả, giới hạn, công thức, bảng sai khác: báo cáo <code>latex/</code> · <code>docs/DEVIATIONS.md</code>

</div>

<!--
Cảm ơn. Câu hỏi thường gặp đã chuẩn bị trong note.txt (RGCN vs GCN, 12 quan hệ, vì sao benign idle, kết
quả demo, công thức điểm subgraph...). Công thức RGCN, điểm subgraph và bảng sai khác đầy đủ nằm trong báo cáo.
-->
