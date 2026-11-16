---
theme: default
title: Phát hiện bất thường trên endpoint device log dựa trên OCR-APT
info: |
  NT2102 – Đồ án phát hiện bất thường trên log thiết bị đầu cuối.
  Demo trực tiếp OCR-APT: demo chính trên ws-etw-0 (exp08), kết quả thứ hai trên ws-etw-1 (exp15-ws1).
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
lõi (5 bước) → demo (video chính trên ws-etw-0 + tấn công thật của thành viên trên ws-etw-1) → cảm ơn. Chi tiết đầy đủ nằm trong báo cáo (latex/).
-->

---
clicks: 2
---

# 1 · Bài toán & OCR-APT

<div class="cmp small" style="grid-template-columns: 1fr 1.25fr; gap:0.8rem">
<div v-click="1" class="card atk">

**Cách cũ**
- Rule → **bỏ sót** kỹ thuật mới

</div>
<div v-click="2" class="card det">

**OCR-APT** (CCS 2025)
- **Subgraph** · **behavior** · **LLM**
- Mục tiêu: tấn công **đỏ**, benign **xanh**, live · **mỗi host một mô hình**

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
Mục tiêu đồ án rất hẹp: demo phần lõi chạy sống trên Windows host (mỗi host một mô hình), không đua số liệu với bài báo.
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
clicks: 5
---

# 2 · ③ RGCN: một trọng số cho mỗi loại edge

<div class="small -mt-2 mb-1">

**R**elational **G**raph **C**onvolutional **N**etwork · GNN cho đồ thị có **nhiều loại edge**

</div>

<RgcnLayer />

<div class="small mt-2 text-center">

**$W_r$ riêng** mỗi loại edge · `Load` ≠ `Write` · 15 feature → **embedding 32 chiều**

</div>

<!--
RGCN = Relational Graph Convolutional Network: một GNN. Ý tưởng GNN (message passing): mỗi lớp, mọi node gộp
thông tin từ chính nó và các neighbor rồi cập nhật vectơ; xếp L lớp thì nhìn xa L bước (OCR-APT 3 lớp, exp08 2 lớp).
"Relational" = đồ thị có nhiều loại edge. Mỗi loại edge (Write/Read/Send/Recv/Start/Load) có ma trận trọng số riêng W_r, nên
mô hình học mỗi loại "kéo" bao nhiêu. Animation: rundll32 gộp chính nó + 2 file + 1 flow, mỗi loại qua
trọng số riêng → 0.1. Sau 2–3 lớp, 15 feature thô thành embedding 32 chiều mang cả chuỗi tài liệu →
powershell → DLL → rundll32 → mạng. Vì sao RGCN chứ không GCN: note.txt Q1; 12 quan hệ: Q3; công thức: báo cáo §2.3.
-->

---
clicks: 3
---

# 2 · ③ Nhiều lớp: nhìn xa L bước

<StackedLayers />

<!--
Mỗi lớp, mọi node cập nhật vectơ từ chính nó và các neighbor (message passing — RGCN làm đúng việc này, chỉ thêm
trọng số riêng theo loại edge). Mỗi lớp nhìn xa thêm một bước: A chỉ nhận được màu của E ở lớp 3. Vectơ sau mỗi
lớp là hidden layer (mô hình tự tính, không có trong log); lớp cuối là embedding, đem vào quả cầu one-class.
Bài báo dùng 3 lớp; nhóm dùng 2 trên exp08 (nông hơn, bớt nhạy với nhiễu ngữ cảnh). Quá nhiều lớp thì mọi node
trộn thành một màu xám (over-smoothing): note.txt Q6. Ví dụ số: note.txt Q5.
-->

---
clicks: 2
---

# 2 · ③ OCRGCN: học "bình thường", gắn cờ cái ở xa

<div class="small -mt-2 mb-1">

**O**ne-**C**lass **R**elational **G**raph **C**onvolutional **N**etwork

</div>

<Hypersphere />

<div class="grid gap-4 small mt-1" style="grid-template-columns: 1fr 1fr 1.2fr; text-align:center">
<div>Không nhãn → chỉ học <b>benign</b></div>
<div>Trong cầu = <b>quen</b> · ngoài cầu = <b>gắn cờ</b></div>
<div>

$s_i = \lVert \mathbf z_i - \mathbf c \rVert^2 - R^2 > \theta$

</div>
</div>

<!--
Không có nhãn nên chỉ học "bình thường": nhốt benign embedding vào một quả cầu (tâm c, bán kính R), cái
gì ra ngoài thì nghi ngờ — tinh thần One-Class SVM. OCRGCN = One-Class RGCN: bài báo mô tả là "RGCN kết hợp one-class SVM" (§1, §5.1.1) nhưng không viết rõ tên đầy đủ, nên "One-Class RGCN" là cách đọc của nhóm từ mô tả đó. Bản của nhóm = encoder RGCN + loss kiểu Deep SVDD. sᵢ là
khoảng cách tới tâm trừ bán kính; vượt threshold θ thì gắn cờ. Điểm then chốt: một mô hình cho MỖI loại
node, vì "bình thường" của process/file/mạng khác nhau. Encoder vẫn nhìn toàn đồ thị; chỉ loss/threshold
theo loại. β, threshold, Hình 2 bài báo: báo cáo §2.4–2.5.
-->

---

# 2 · ③ OCRGCN: huấn luyện (Training)

<img src="./images/paper-fig2-train.png" class="fig" style="max-height:300px;object-fit:contain" />
<div class="src">Hình 2 (trái) của Aly et al., CCS 2025: pha Training</div>

<div class="small mt-2">

- Chỉ **benign** · **mỗi loại node một mô hình** (Process · File · Network Flow)
- **Estimate Contamination** γ → đặt threshold θ
- **RGCN** → embedding → **hypersphere** (One-Class SVM)

</div>

<!--
Pha huấn luyện (Hình 2 trái). Đồ thị chỉ gồm benign node → mã hoá + 15 feature → tách theo loại node.
Mỗi loại một OCRGCN: ước lượng contamination γ (tỉ lệ điểm huấn luyện được phép vượt threshold; θ = quantile
1 − γ của điểm huấn luyện; bài báo kẹp γ trong [0.001, 0.05], không nhãn thì 0.001 — nhóm dùng 0.001), qua nhiều
lớp RGCN ra embedding, rồi học hypersphere (tâm c, bán kính R). Kết quả: 3 mô hình. Encoder vẫn chạy trên toàn
đồ thị; chỉ loss/hypersphere/threshold tính riêng theo loại. Báo cáo §2.4–2.5 (Hình tsmod).
-->

---

# 2 · ③ OCRGCN: suy luận (Inference)

<img src="./images/paper-fig2-infer.png" class="fig" style="max-height:300px;object-fit:contain" />
<div class="src">Hình 2 (phải) của Aly et al., CCS 2025: pha Inference</div>

<div class="small mt-2">

- Đồ thị mới (có thể có tấn công) → **cùng** feature + scaler
- Mỗi node → mô hình **đúng loại** → khoảng cách tới tâm
- Ngoài cầu = **anomalous node + score** → bước ④

</div>

<!--
Pha suy luận (Hình 2 phải). Đồ thị cần kiểm tra đi qua đúng các bước mã hoá + feature (dùng lại scaler của
lúc train); mỗi node được chấm bởi mô hình của loại mình, sᵢ > θ của loại đó thì là anomalous node. Đầu ra:
danh sách anomalous node kèm điểm → đưa vào bước ④ dựng subgraph. Khác bài báo: bài báo chấm cả log một lần
(offline); bản live của nhóm chấm từng window 5 phút (DEVIATIONS: windowed detection).
-->

---

# 2 · ④⑤ Subgraph → cảnh báo → LLM

<div class="cmp mt-4">
<div class="card det">

### ④ Alg. 1 + SG-Det
- Nối node bị cờ → **subgraph**
- Điểm = **Σ xác suất** node bị cờ
- **≥ 10 → Moderate → đỏ** (thang bài báo)
- Live hai máy: **≥ 4.5**

</div>
<div class="card inv">

### ⑤ LLM
- Subgraph → **IOC** → lọc hallucination → **báo cáo**
- **triage** trước: `attack` / `benign` / `uncertain`
- **Overlay**: không xoá cảnh báo, không vào training

</div>
</div>

<!--
Node rời rạc → subgraph có điểm → báo cáo. Điểm subgraph là TỔNG xác suất (min-max, kẹp [0,1]) của node
bị cờ, nên ≥10 ≈ ít nhất ~10 node bất thường nối thành một câu chuyện. Thang Minor <10, Moderate 10–100,
Significant 100–1000, Critical >1000 (§7.2.1); không dùng top-k fallback vì sẽ làm mọi window đỏ.
Video demo chính (exp08) dùng đúng mốc 10; live trên hai máy dùng mốc 4.5 (≈ 5 node bất thường liên thông).
LLM đọc subgraph, trích IOC, lọc phần bịa, viết báo cáo; nhóm thêm bước phân loại trước (bài báo luôn giả
định tấn công). LLM chỉ là lớp phủ (rủi ro prompt injection). Công thức: báo cáo Phụ lục.
-->

---

# 3 · Demo: hạ tầng lab (Proxmox)

<div style="max-width:84%;margin:0 auto"><LabInfra /></div>

<div class="tiny muted mt-1 text-center">Terraform + Ansible · <b>hai</b> Windows host, mỗi host một mô hình</div>

<!--
Lab trên một hypervisor Proxmox. ws-etw-0 chạy DVWA (web có lỗ hổng) và etwcollect (collector ETW nhóm tự viết);
từ 09-11 nó còn là Domain Controller (video demo chính quay trước đó, mô hình exp08). ws-etw-1 là máy thứ hai:
ứng dụng Next.js có lỗ hổng RCE (react2shell) do một thành viên dựng, mô hình riêng exp15-ws1.
ubuntu-trainer chạy Loki, Grafana, huấn luyện và ocr-live (máy chủ demo, một engine cho mỗi host).
① Kali khai thác DVWA, ② log ETW của cả hai host đẩy về Loki, ③ ocr-live phục vụ giao diện cảnh báo,
④ thành viên tấn công thật ws-etw-1 từ máy mình qua VPN. Correlation giữa hai host: đã viết, chưa kiểm chứng.
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
layout: center
class: text-center
---

<h1 v-motion :initial="{ scale: 0.9, opacity: 0 }" :enter="{ scale: 1, opacity: 1, transition: { duration: 600 } }" style="font-size:2.4rem;color:#1e3a8a;margin-bottom:0.6rem">3 · Demo</h1>

<img src="./images/ws1-attack-flow.png" class="mx-auto" style="max-height:380px;object-fit:contain;border-radius:8px;box-shadow:0 4px 14px rgba(0,0,0,0.25)" v-motion :initial="{ y: 20, opacity: 0 }" :enter="{ y: 0, opacity: 1, transition: { duration: 600, delay: 200 } }" />

<!--
Hình: kịch bản tấn công của thành viên (react2shell trên ws-etw-1 10.21.3.11 → C2 → leo quyền → LDAP tới DC
ws-etw-0 10.21.3.10 → ADCS ESC1 → Administrator). Đây là sơ đồ kịch bản, không phải kết quả phát hiện:
phần cross-host (bước 07–09) chưa được kiểm chứng là đỏ trên DC.
Demo 1 (ws-etw-0, exp08): video đã quay sẵn (YouTube: https://www.youtube.com/watch?v=Jzv03v82bNo). Cần mạng để phát;
nếu mất mạng, mô tả bằng lời: ① idle xanh → ② tấn công chạy → ③ window đỏ + subgraph → ④ kết luận LLM.
Kết quả đo được (exp08: window tấn công 05-11 17:10Z 70.17 / LLM attack 0.85; 24 h: 288 window, 7 alert, 3 false alarm)
và giới hạn nằm trong báo cáo §3–4; nếu được hỏi: note.txt Q7.
Demo 2 (ws-etw-1, exp15-ws1): video thành viên quay lần tấn công react2shell (15-11, 03:35–04:40Z). Kết quả
(04:15Z 7.24 · 04:20Z 19.86 đỏ, LLM attack 0.85/0.95, mốc Moderate 4.5) nằm trong báo cáo §R4.
-->

---
layout: center
class: text-center
transition: fade
---

<img src="./images/logo-uit.jpg" class="h-20 mx-auto mb-6" v-motion :initial="{ scale: 0.6, opacity: 0 }" :enter="{ scale: 1, opacity: 1, transition: { duration: 600 } }" />

<h1 v-motion :initial="{ y: 30, opacity: 0 }" :enter="{ y: 0, opacity: 1, transition: { duration: 700, delay: 200 } }" style="font-size:2.6rem;color:#1e3a8a">Cảm ơn thầy và các bạn đã lắng nghe!</h1>

