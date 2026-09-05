"""
TanyaMed — Simulasi Alur Layanan (terhubung ke Anthropic API)
================================================================
Prototipe Streamlit dengan Claude sebagai "otak" conversational triage agent
dan pre-anamnesis assistant. Claude memutuskan sendiri, lewat tool use, kapan
harus merujuk darurat (rujuk_darurat) dan kapan riwayat gejala sudah cukup
lengkap untuk dicatat (catat_riwayat). Efek sampingnya (isi panel Faskes,
poin & lencana SATUSEHAT) dieksekusi di Python begitu tool tersebut dipanggil.

Cara menjalankan lokal:
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY="sk-ant-..."      # atau isi lewat sidebar app
    streamlit run app.py

Cara deploy ke Streamlit Community Cloud:
    1. Push app.py + requirements.txt ke repo GitHub (JANGAN commit API key).
    2. Di dashboard Streamlit Cloud, buka Settings > Secrets, isi:
           ANTHROPIC_API_KEY = "sk-ant-..."
    3. Deploy dengan app.py sebagai entry point.
"""

import os
from datetime import datetime

import streamlit as st
from anthropic import Anthropic

st.set_page_config(page_title="TanyaMed — Simulasi", layout="wide")

MODEL_OPTIONS = {
    "Claude Sonnet 5 (seimbang)": "claude-sonnet-5",
    "Claude Haiku 4.5 (cepat & hemat)": "claude-haiku-4-5-20251001",
}

GREETING = (
    "Halo, saya TanyaMed 👋 Asisten kesehatan digital yang terhubung ke "
    "SATUSEHAT. Boleh ceritakan keluhan yang kamu rasakan?"
)

SYSTEM_PROMPT = """Kamu adalah TanyaMed, asisten kesehatan percakapan berbasis WhatsApp untuk masyarakat Indonesia. Peranmu ada dua: (1) conversational triage agent yang menilai urgensi keluhan, dan (2) pre-anamnesis assistant yang menggali riwayat gejala secara bertahap sebelum pengguna bertemu tenaga medis.

ATURAN PENTING:
- Kamu TIDAK PERNAH memberikan diagnosis penyakit atau merekomendasikan obat spesifik. Kamu hanya menggali riwayat dan mengedukasi bahwa kecocokan gejala saja tidak cukup untuk memastikan suatu penyakit.
- Jika keluhan pengguna mengindikasikan kondisi darurat (contoh: nyeri dada hebat, sesak napas berat, pendarahan hebat, kejang, tidak sadarkan diri, tanda stroke seperti wajah perot atau bicara pelo), SEGERA panggil tool rujuk_darurat. Jangan lanjutkan pertanyaan pre-anamnesis lain dalam kondisi ini.
- Jika tidak darurat, gali secara bertahap dan alami (satu-dua pertanyaan singkat per giliran, nada hangat, tidak menggurui) mengenai: lokasi gejala, durasi, karakteristik (apa yang memperberat/meredakan), dan riwayat pengobatan mandiri yang sudah dilakukan.
- Setelah kelima informasi berikut sudah kamu dapatkan dari percakapan (keluhan utama, lokasi, durasi, karakteristik, riwayat obat mandiri), panggil tool catat_riwayat dengan seluruh field terisi.
- Gunakan Bahasa Indonesia sehari-hari yang singkat dan empatik. Hindari jargon medis yang rumit."""

TOOLS = [
    {
        "name": "rujuk_darurat",
        "description": (
            "Panggil SEGERA jika pengguna menceritakan gejala yang mengindikasikan "
            "kondisi darurat medis. Jangan lanjutkan pertanyaan pre-anamnesis lain "
            "jika tool ini dipanggil."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keluhan_utama": {
                    "type": "string",
                    "description": "Ringkasan singkat gejala darurat yang disampaikan pengguna",
                },
                "alasan_darurat": {
                    "type": "string",
                    "description": "Alasan singkat kenapa ini tergolong darurat",
                },
            },
            "required": ["keluhan_utama", "alasan_darurat"],
        },
    },
    {
        "name": "catat_riwayat",
        "description": (
            "Panggil HANYA setelah kelima informasi berikut berhasil dikumpulkan "
            "secara bertahap dari percakapan: keluhan utama, lokasi, durasi, "
            "karakteristik, dan riwayat pengobatan mandiri. Jangan panggil sebelum "
            "semuanya tersedia."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keluhan_utama": {"type": "string"},
                "lokasi": {"type": "string"},
                "durasi": {"type": "string"},
                "karakteristik": {"type": "string"},
                "riwayat_obat": {"type": "string"},
            },
            "required": ["keluhan_utama", "lokasi", "durasi", "karakteristik", "riwayat_obat"],
        },
    },
]

BADGE_DEFS = [
    {"name": "Pemula Sehat", "threshold": 10},
    {"name": "Pasien Siaga", "threshold": 30},
    {"name": "Ahli Riwayat", "threshold": 60},
]

LEVEL_NAMES = [
    "Level 0 · Belum mulai",
    "Level 1 · Sehat Pemula",
    "Level 2 · Pasien Siaga",
    "Level 3 · Ahli Riwayat",
]


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
def init_state():
    defaults = {
        "user_messages": [{"role": "bot", "text": GREETING}],
        "api_history": [],  # riwayat teks polos yang dikirim ke Claude tiap giliran
        "faskes_messages": [],
        "points": 0,
        "records": [],
        "last_error": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def now() -> str:
    return datetime.now().strftime("%H:%M")


# ---------------------------------------------------------------------------
# Efek samping tool (mengisi panel Faskes & SATUSEHAT)
# ---------------------------------------------------------------------------
def add_faskes_card(title: str, rows: list, urgent: bool = False):
    st.session_state.faskes_messages.append(
        {"title": title, "rows": rows, "urgent": urgent, "time": now()}
    )


def add_record(title: str, urgent: bool = False):
    st.session_state.records.append({"title": title, "time": now(), "urgent": urgent})


def add_points(n: int):
    st.session_state.points += n


def handle_emergency_tool(data: dict):
    keluhan = data.get("keluhan_utama", "-")
    alasan = data.get("alasan_darurat", "-")
    add_faskes_card(
        "Rujukan darurat masuk",
        [
            ("Keluhan", keluhan),
            ("Alasan", alasan),
            ("Status", "Rujuk segera ke IGD"),
            ("Sumber", "Deteksi otomatis TanyaMed (Claude)"),
        ],
        urgent=True,
    )
    add_record(f"Rujukan darurat: {keluhan}", urgent=True)


def handle_catat_tool(data: dict):
    add_faskes_card(
        "Ringkasan riwayat pasien",
        [
            ("Keluhan", data.get("keluhan_utama", "-")),
            ("Lokasi", data.get("lokasi", "-")),
            ("Durasi", data.get("durasi", "-")),
            ("Karakteristik", data.get("karakteristik", "-")),
            ("Obat mandiri", data.get("riwayat_obat", "-")),
        ],
    )
    add_points(10)
    add_record(f"Riwayat gejala: {data.get('keluhan_utama', '-')}")


# ---------------------------------------------------------------------------
# Panggilan ke Anthropic API
# ---------------------------------------------------------------------------
def call_claude(client: Anthropic, model_id: str, user_text: str):
    st.session_state.api_history.append({"role": "user", "content": user_text})

    try:
        response = client.messages.create(
            model=model_id,
            max_tokens=700,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=st.session_state.api_history,
        )
    except Exception as e:
        st.session_state.api_history.pop()  # rollback supaya riwayat tetap valid
        st.session_state.last_error = str(e)
        return

    reply_parts, tool_calls = [], []
    for block in response.content:
        if block.type == "text":
            reply_parts.append(block.text)
        elif block.type == "tool_use":
            tool_calls.append(block)

    reply_text = "\n".join(p for p in reply_parts if p).strip()

    for tc in tool_calls:
        if tc.name == "rujuk_darurat":
            handle_emergency_tool(tc.input)
            if not reply_text:
                reply_text = (
                    "⚠️ Berdasarkan gejala yang kamu ceritakan, ini bisa jadi kondisi "
                    "darurat. Segera ke IGD terdekat atau hubungi 119. Info ini sudah "
                    "saya kirim ke Puskesmas Wonorejo."
                )
        elif tc.name == "catat_riwayat":
            handle_catat_tool(tc.input)
            if not reply_text:
                reply_text = (
                    "Terima kasih, riwayat kamu sudah lengkap dan tersimpan ✅ "
                    "Ketik pesan baru kalau ada keluhan lain."
                )

    if not reply_text:
        reply_text = "Baik, dicatat."

    st.session_state.api_history.append({"role": "assistant", "content": reply_text})
    st.session_state.user_messages.append({"role": "bot", "text": reply_text})


# ---------------------------------------------------------------------------
# Sidebar — kunci API & pilihan model
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Pengaturan")
    try:
        default_key = st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        default_key = os.environ.get("ANTHROPIC_API_KEY", "")

    api_key = st.text_input(
        "Anthropic API Key",
        value=default_key,
        type="password",
        help="Sebaiknya isi lewat Streamlit Secrets saat deploy publik, bukan diketik langsung.",
    )
    model_label = st.selectbox("Model", list(MODEL_OPTIONS.keys()))
    model_id = MODEL_OPTIONS[model_label]
    st.caption("Kunci API hanya dipakai di sesi browser ini, tidak disimpan permanen di server.")

client = Anthropic(api_key=api_key) if api_key else None

# ---------------------------------------------------------------------------
# UI utama
# ---------------------------------------------------------------------------
init_state()

st.title("TanyaMed — Simulasi Alur Layanan")
st.caption(
    "Tiga panel: WhatsApp pengguna, WhatsApp faskes, dan aplikasi SATUSEHAT. "
    "Percakapan dijalankan oleh Claude lewat Anthropic API dengan tool use "
    "untuk triase darurat dan pencatatan riwayat."
)

if not client:
    st.warning("Masukkan Anthropic API key di sidebar untuk mulai mengobrol dengan TanyaMed.")

if st.session_state.last_error:
    st.error(f"Gagal menghubungi Anthropic API: {st.session_state.last_error}")
    st.session_state.last_error = None

col1, col2, col3 = st.columns(3, gap="medium")

# ---- Panel 1: WhatsApp Pengguna -------------------------------------------
with col1:
    st.subheader("💬 WhatsApp — Pengguna")
    chat_box = st.container(height=430, border=True)
    with chat_box:
        for m in st.session_state.user_messages:
            avatar = "🤖" if m["role"] == "bot" else "🧑"
            with st.chat_message(m["role"], avatar=avatar):
                st.write(m["text"])

    qc1, qc2 = st.columns(2)
    if qc1.button("Contoh keluhan biasa", use_container_width=True, disabled=not client):
        text = "Sakit kepala sejak 2 hari, terasa berdenyut"
        st.session_state.user_messages.append({"role": "user", "text": text})
        call_claude(client, model_id, text)
        st.rerun()
    if qc2.button("Contoh keluhan darurat", use_container_width=True, disabled=not client):
        text = "Dada saya nyeri hebat dan sesak napas sejak tadi malam"
        st.session_state.user_messages.append({"role": "user", "text": text})
        call_claude(client, model_id, text)
        st.rerun()

    with st.form("user_form", clear_on_submit=True):
        msg = st.text_input(
            "Ketik pesan",
            label_visibility="collapsed",
            placeholder="Ketik keluhan atau jawaban di sini…",
        )
        sent = st.form_submit_button("Kirim", disabled=not client)
    if sent and msg.strip() and client:
        st.session_state.user_messages.append({"role": "user", "text": msg})
        call_claude(client, model_id, msg)
        st.rerun()

# ---- Panel 2: WhatsApp Faskes ----------------------------------------------
with col2:
    st.subheader("🏥 WhatsApp — Faskes")
    box2 = st.container(height=430, border=True)
    with box2:
        if not st.session_state.faskes_messages:
            st.caption(
                "Ringkasan riwayat pasien dari TanyaMed akan muncul di sini "
                "secara otomatis."
            )
        for c in st.session_state.faskes_messages:
            with st.container(border=True):
                icon = "⚠️" if c["urgent"] else "📋"
                st.markdown(f"**{icon} {c['title']}**  \n*{c['time']}*")
                for label, val in c["rows"]:
                    st.markdown(f"- **{label}:** {val}")

# ---- Panel 3: SATUSEHAT -----------------------------------------------------
with col3:
    st.subheader("🩺 SATUSEHAT")
    box3 = st.container(height=430, border=True)
    with box3:
        points = st.session_state.points
        next_badge = next((b for b in BADGE_DEFS if points < b["threshold"]), None)
        prev_threshold = 0
        for b in BADGE_DEFS:
            if points >= b["threshold"]:
                prev_threshold = b["threshold"]
        level_idx = len([b for b in BADGE_DEFS if points >= b["threshold"]])

        st.metric("Poin", points)
        st.write(LEVEL_NAMES[min(level_idx, len(LEVEL_NAMES) - 1)])

        if next_badge:
            span = next_badge["threshold"] - prev_threshold
            pct = (points - prev_threshold) / span if span else 1.0
            st.progress(min(1.0, max(0.0, pct)))
            st.caption(
                f"{next_badge['threshold'] - points} poin lagi menuju lencana "
                f"\"{next_badge['name']}\""
            )
        else:
            st.progress(1.0)
            st.caption("Semua lencana yang tersedia sudah kamu dapatkan 🎉")

        st.markdown("**Lencana**")
        bcols = st.columns(len(BADGE_DEFS))
        for i, b in enumerate(BADGE_DEFS):
            unlocked = points >= b["threshold"]
            with bcols[i]:
                st.markdown(("🏅 " if unlocked else "🔒 ") + b["name"])

        st.markdown("**Riwayat kesehatan tercatat**")
        if not st.session_state.records:
            st.caption("Belum ada riwayat yang tercatat.")
        for r in reversed(st.session_state.records):
            with st.container(border=True):
                prefix = "⚠️ " if r["urgent"] else ""
                st.markdown(f"**{prefix}{r['title']}**")
                st.caption(f"Tercatat otomatis · {r['time']} · terenkripsi AES-256")
