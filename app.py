"""app.py — SIPINTER (Sistem Informasi Peminjaman Interaktif dan Nyaman Terpadu)."""
import base64
import json
import logging
import os
import streamlit as st
import pandas as pd
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import database as db

# ================= KONFIG =================
st.set_page_config(page_title="SIPINTER", layout="wide")

css = Path(__file__).parent / "assets" / "style.css"
logo_path = Path(__file__).parent / "assets" / "logo-sipinter.png"
if css.exists():
    st.markdown(f"<style>{css.read_text()}</style>", unsafe_allow_html=True)

st.markdown("""
<style>
.nota-box { font-family: 'Courier New', Courier, monospace; background: #fff; padding: 25px; border: 1px solid #ddd; max-width: 550px; margin: 20px auto; color: #000; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
.nota-header { text-align: center; border-bottom: 2px dashed #000; padding-bottom: 15px; margin-bottom: 15px; }
.nota-header h2 { margin: 0; font-size: 22px; letter-spacing: 1px; }
.nota-header p { margin: 5px 0 0 0; font-size: 13px; color: #555; }
.nota-table { width: 100%; font-size: 14px; margin-bottom: 15px; border-collapse: collapse; }
.nota-table td { padding: 6px 0; vertical-align: top; }
.nota-table td:first-child { width: 35%; font-weight: bold; }
.nota-footer { text-align: center; border-top: 2px dashed #000; padding-top: 15px; margin-top: 15px; font-size: 12px; font-style: italic; }
@media print { .no-print { display: none !important; } .nota-box { box-shadow: none; border: none; margin: 0; padding: 0; } }

.sidebar-logo { text-align: center; padding: 20px 0 30px 0; }
.sidebar-logo h2 { margin: 0; color: #1e3a8a; font-size: 24px; font-weight: 800; letter-spacing: 2px; }

div.stButton > button[kind="primary"] { background: #365a7a; color: white; border: 1px solid #365a7a; border-radius: 8px; font-weight: 600; }
div.stButton > button[kind="primary"]:hover { background: #2d4d69; border-color: #2d4d69; color: white; }
</style>
""", unsafe_allow_html=True)

db.inisialisasi()
db.refresh_status()

for k, v in {"halaman": "Dashboard", "detail_trx": None, "detail_pem": None, "detail_buk": None, "flash": None, "mode_tambah": False}.items():
    st.session_state.setdefault(k, v)

# ================= UTIL =================
BULAN = ["Jan","Feb","Mar","Apr","Mei","Jun","Jul","Agu","Sep","Okt","Nov","Des"]

def tgl(t):
    if not t: return "-"
    if isinstance(t, str):
        try: t = datetime.strptime(t, "%Y-%m-%d").date()
        except ValueError: return t
    return f"{t.day} {BULAN[t.month-1]} {t.year}"

def rp(n): return f"Rp{int(n):,}".replace(",", ".")

def badge(s):
    cfg = {
        "DIPINJAM": ("#1e3a8a", "DIPINJAM"),
        "TERLAMBAT": ("#b45309", "TERLAMBAT"),
        "DIKEMBALIKAN": ("#4b5563", "DIKEMBALIKAN"),
    }
    c, teks = cfg.get(s, ("#0d6efd", s))
    return (f'<span style="background:{c};color:#fff;padding:4px 12px;border-radius:12px;font-size:12px;font-weight:600">{teks}</span>')

def kirim_whatsapp(no, pesan):
    token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    api_version = os.getenv("WHATSAPP_API_VERSION", "v22.0")
    if not token or not phone_number_id:
        return False, (
            "WhatsApp belum dikonfigurasi. Atur WHATSAPP_ACCESS_TOKEN dan "
            "WHATSAPP_PHONE_NUMBER_ID pada environment aplikasi."
        )

    payload = json.dumps({
        "messaging_product": "whatsapp",
        "to": no,
        "type": "text",
        "text": {"body": pesan},
    }).encode("utf-8")
    request = Request(
        f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(detail).get("error", {}).get("message", detail)
        except json.JSONDecodeError:
            pass
        return False, f"WhatsApp API menolak pesan: {detail}"
    except URLError as error:
        return False, f"Tidak dapat menghubungi WhatsApp API: {error.reason}"
    except json.JSONDecodeError:
        return False, "Respons WhatsApp API tidak dapat dibaca."

    if not isinstance(response_data, dict):
        return False, "Respons WhatsApp API memiliki format yang tidak valid."
    if not response_data.get("messages"):
        return False, "WhatsApp API tidak mengonfirmasi pengiriman pesan."
    return True, "Pesan WhatsApp berhasil dikirim."

def url_whatsapp(no, pesan):
    nomor = "".join(filter(str.isdigit, str(no)))
    if not nomor:
        return None
    return f"https://wa.me/{nomor}?text={quote(pesan, safe='')}"

def catat_log(id_trx, jenis, no, pesan, status):
    db.x("""INSERT INTO log_whatsapp (id_transaksi,jenis,no_tujuan,isi_pesan,status,waktu_kirim)
            VALUES (?,?,?,?,?,?)""", (id_trx, jenis, no, pesan, status, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

def kirim_dan_catat_whatsapp(id_trx, jenis, no, pesan):
    berhasil, hasil = kirim_whatsapp(no, pesan)
    status = "TERKIRIM" if berhasil else f"GAGAL: {hasil}"
    catat_log(id_trx, jenis, no, pesan, status)
    if not berhasil:
        logging.warning(
            "WhatsApp delivery failed for transaction %s (%s): %s",
            id_trx,
            jenis,
            hasil,
        )
    return berhasil, hasil

def flash(msg, tipe="success"): st.session_state["flash"] = (tipe, msg)
def tampil_flash():
    if st.session_state["flash"]:
        tipe, msg = st.session_state["flash"]; st.session_state["flash"] = None
        notifier = {
            "success": st.success,
            "warning": st.warning,
            "error": st.error,
        }.get(tipe, st.info)
        notifier(msg)

def buka_detail_trx(i): st.session_state.update(detail_trx=i, detail_pem=None, detail_buk=None)
def buka_detail_pem(i): st.session_state.update(detail_pem=i, detail_trx=None, detail_buk=None)
def buka_detail_buk(i): st.session_state.update(detail_buk=i, detail_trx=None, detail_pem=None)
def tutup_detail(): st.session_state.update(detail_trx=None, detail_pem=None, detail_buk=None)

def ambil_transaksi_lengkap(id_trx):
    rows = db.q("""SELECT t.*, p.nim, p.nama AS nama_peminjam, p.program_studi, p.jurusan, p.kelas, p.no_whatsapp, b.kode_buku, b.judul, b.isbn
                   FROM transaksi t JOIN peminjam p ON p.id=t.id_peminjam JOIN buku b ON b.id=t.id_buku WHERE t.id=?""", (id_trx,))
    return rows[0] if rows else None

# ================= TEMPLATE WA =================
def wa_sukses_pinjam(t, p, b, tgl_tenggat):
    return f"""Yth. {p['nama']},\n\nPeminjaman buku Anda berhasil dicatat.\n\n*Bukti Transaksi*\nKode transaksi: {t['kode_transaksi']}\nNama peminjam: {p['nama']}\nNIM: {p['nim']}\nBuku: {b['judul']}\nTanggal pinjam: {tgl(t['tanggal_pinjam'])}\nBatas pengembalian: {tgl_tenggat}\n\nSilakan simpan kode transaksi ini sebagai bukti peminjaman.\n\nAdministrasi SIPINTER"""

def wa_sukses_kembali(t, p, b, denda):
    return f"""Yth. {p['nama']},\n\nPengembalian buku Anda berhasil dicatat.\n\n*Bukti Transaksi*\nKode transaksi: {t['kode_transaksi']}\nNama peminjam: {p['nama']}\nBuku: {b['judul']}\nTanggal kembali: {tgl(t['tanggal_kembali'])}\nDenda: {rp(denda)} (lunas)\n\nTerima kasih telah menggunakan layanan SIPINTER."""

def wa_terlambat(t, p, b, hari, denda):
    return f"""Yth. {p['nama']},\n\nBerdasarkan catatan sistem, peminjaman buku Anda telah melewati batas waktu.\n\n*Buku:* {b['judul']}\n*Batas Kembali:* {tgl(t['tanggal_tenggat'])}\n*Keterlambatan:* {hari} hari\n*Total Denda:* Rp{denda:,}\n\nMohon segera kembalikan buku. Terima kasih.\n\nAdministrasi SIPINTER""".replace(",", ".")

# ================= SIDEBAR =================
def render_sidebar():
    nav_icons = {
        "Peminjaman": ":material/bookmark_add:",
        "Pengembalian": ":material/assignment_return:",
        "Transaksi": ":material/receipt_long:",
        "Denda": ":material/payments:",
        "Buku": ":material/menu_book:",
        "Anggota": ":material/group:",
        "Penulis": ":material/edit_note:",
        "Penerbit": ":material/business:",
    }
    with st.sidebar:
        _, logo_center, _ = st.columns([1, 2, 1])
        with logo_center:
            st.image(str(logo_path), width=120)
        st.markdown('<div class="sidebar-logo"><h2>SIPINTER</h2></div>', unsafe_allow_html=True)
        
        aktif = "menu-active" if st.session_state["halaman"] == "Dashboard" else ""
        st.markdown(f'<div class="{aktif}">', unsafe_allow_html=True)
        if st.button(
            "Dashboard", key="nav_dash", icon=":material/dashboard:",
            type="primary" if aktif else "secondary", use_container_width=True,
        ):
            st.session_state.update(halaman="Dashboard", mode_tambah=False); tutup_detail(); st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="menu-kategori">PROSES</div>', unsafe_allow_html=True)
        for nama in ["Peminjaman", "Pengembalian"]:
            aktif = "menu-active" if st.session_state["halaman"] == nama else ""
            st.markdown(f'<div class="{aktif}">', unsafe_allow_html=True)
            if st.button(
                nama, key=f"nav_{nama}", icon=nav_icons[nama],
                type="primary" if aktif else "secondary", use_container_width=True,
            ):
                st.session_state.update(halaman=nama, mode_tambah=False); tutup_detail(); st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="menu-kategori">TRANSAKSI</div>', unsafe_allow_html=True)
        for nama in ["Transaksi", "Denda"]:
            aktif = "menu-active" if st.session_state["halaman"] == nama else ""
            st.markdown(f'<div class="{aktif}">', unsafe_allow_html=True)
            if st.button(
                nama, key=f"nav_{nama}", icon=nav_icons[nama],
                type="primary" if aktif else "secondary", use_container_width=True,
            ):
                st.session_state.update(halaman=nama, mode_tambah=False); tutup_detail(); st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="menu-kategori">MASTER DATA</div>', unsafe_allow_html=True)
        for nama in ["Buku", "Anggota", "Penulis", "Penerbit"]:
            aktif = "menu-active" if st.session_state["halaman"] == nama else ""
            st.markdown(f'<div class="{aktif}">', unsafe_allow_html=True)
            if st.button(
                nama, key=f"nav_{nama}", icon=nav_icons[nama],
                type="primary" if aktif else "secondary", use_container_width=True,
            ):
                st.session_state.update(halaman=nama, mode_tambah=False); tutup_detail(); st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

# ================= HALAMAN: DASHBOARD =================
def halaman_dashboard():
    logo_data = base64.b64encode(logo_path.read_bytes()).decode("ascii")
    with st.container():
        st.markdown(
            f"""
            <div style="display:flex;align-items:center;gap:28px;background:#f8fafc;
                        border:1px solid #e2e8f0;border-radius:8px;padding:20px 28px;
                        margin-bottom:24px">
                <img src="data:image/png;base64,{logo_data}" alt="Logo SIPINTER"
                     style="width:150px;height:auto">
                <div>
                    <h1 style="margin:0;color:#1e3a8a;font-size:32px">SIPINTER</h1>
                    <p style="margin:8px 0 0;color:#475569">
                        Sistem Informasi Peminjaman Interaktif dan Nyaman Terpadu
                    </p>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    total_judul = db.q("SELECT COUNT(*) n FROM buku")[0]["n"]
    stok = db.q("SELECT COALESCE(SUM(stok_tersedia),0) n FROM buku")[0]["n"]
    dipinjam = db.q("SELECT COUNT(*) n FROM transaksi WHERE status IN ('DIPINJAM','TERLAMBAT')")[0]["n"]
    pem_aktif = db.q("SELECT COUNT(DISTINCT id_peminjam) n FROM transaksi WHERE status IN ('DIPINJAM','TERLAMBAT')")[0]["n"]
    telat = db.q("SELECT COUNT(*) n FROM transaksi WHERE status='TERLAMBAT'")[0]["n"]
    total_denda = db.q("SELECT COALESCE(SUM(total_denda),0) n FROM transaksi WHERE total_denda>0")[0]["n"]

    kartu = [
        ("Total Judul Buku", total_judul),
        ("Stok Tersedia", stok),
        ("Buku Sedang Dipinjam", dipinjam),
        ("Peminjam Aktif", pem_aktif),
        ("Transaksi Terlambat", telat),
        ("Total Denda", rp(total_denda)),
    ]

    cols = st.columns(3)
    for i, (label, val) in enumerate(kartu):
        with cols[i % 3]:
            st.markdown(f"<div class='kartu'><div class='label'>{label}</div><div class='angka'>{val}</div></div>", unsafe_allow_html=True)

    st.markdown("---")
    st.subheader("Daftar Pinjaman Aktif")
    
    with st.container():
        c1, c2, c3, c4, c5, c6 = st.columns([1.5, 2.5, 2.5, 2.5, 2, 1])
        c1.markdown("**ID Transaksi**")
        c2.markdown("**Nama Peminjam**")
        c2.selectbox("Urut Nama", ["A-Z", "Z-A"], key="dash_sort_nama", label_visibility="collapsed")
        
        c3.markdown("**Kategori Buku**")
        c3.selectbox("Kategori", ["Semua"] + list(db.KATEGORI_BUKU.keys()), key="dash_kat_buku", label_visibility="collapsed")
        
        c4.markdown("**Waktu**")
        c4.selectbox("Urut Waktu", ["Terdekat", "Terlama"], key="dash_sort_waktu", label_visibility="collapsed")
        
        c5.markdown("**Status**")
        f_status = c5.selectbox("Filter Status", ["Semua", "DIPINJAM", "TERLAMBAT"], key="dash_filter_status", label_visibility="collapsed")
        
        c6.markdown("**Aksi**")

    sql = """SELECT t.*, p.nama AS nama_peminjam, p.no_whatsapp, p.nim, p.jurusan, p.program_studi, p.kelas,
             b.judul, b.kode_buku, b.kategori FROM transaksi t 
             JOIN peminjam p ON p.id=t.id_peminjam JOIN buku b ON b.id=t.id_buku
             WHERE t.status IN ('DIPINJAM','TERLAMBAT')"""
             
    if f_status != "Semua": sql += f" AND t.status = '{f_status}'"
    if st.session_state["dash_kat_buku"] != "Semua": sql += f" AND b.kategori = '{st.session_state['dash_kat_buku']}'"
    
    if st.session_state["dash_sort_nama"] == "A-Z": sql += " ORDER BY p.nama ASC"
    else: sql += " ORDER BY p.nama DESC"
    
    rows = db.q(sql)
    if not rows: st.info("Tidak ada data."); return

    for t in rows:
        hari, denda = db.hitung_denda(t)
        p = {"nama": t["nama_peminjam"], "nim": t["nim"], "jurusan": t["jurusan"], "program_studi": t["program_studi"], "kelas": t["kelas"]}
        b = {"judul": t["judul"], "kode_buku": t["kode_buku"]}
        
        pesan = wa_terlambat(t, p, b, hari, denda) if t["status"] == "TERLAMBAT" else wa_sukses_pinjam(t, p, b, tgl(t["tanggal_tenggat"]))
        
        c1, c2, c3, c4, c5, c6 = st.columns([1.5, 2.5, 2.5, 2.5, 2, 1])
        if c1.button(t["kode_transaksi"], key=f"d_kode_{t['id']}", icon=":material/visibility:", use_container_width=True):
            buka_detail_trx(t["id"]); st.rerun()
        c2.write(t["nama_peminjam"])
        c3.write(t["kategori"])
        c4.write(tgl(t["tanggal_tenggat"]))
        c5.markdown(badge(t["status"]), unsafe_allow_html=True)
        with c6:
            url_wa = url_whatsapp(t["no_whatsapp"], pesan)
            if url_wa:
                st.link_button(
                    "Kirim WhatsApp", url_wa, key=f"wa_dash_{t['id']}",
                    icon=":material/send:", help="Buka WhatsApp dengan pesan siap dikirim",
                )
            else:
                st.warning("Nomor WhatsApp tidak valid.")

# ================= HALAMAN: TRANSAKSI =================
def halaman_transaksi():
    st.title("Transaksi")

    cari = st.text_input(
        "Cari Transaksi",
        placeholder="Kode transaksi, nama/NIM peminjam, kode/judul buku",
        key="trx_search",
    ).strip()
    
    c1, c2, c3, c4, c5, c6, c7 = st.columns([1.5, 2, 2.5, 1.8, 2, 1.8, 1.5])
    c1.markdown("**ID Transaksi**")
    c2.markdown("**Nama Peminjam**")
    c2.selectbox("Urut Nama", ["A-Z", "Z-A"], key="trx_sort_nama", label_visibility="collapsed")
    
    c3.markdown("**Judul Buku**")
    c4.markdown("**Kategori Buku**")
    c4.selectbox("Kategori", ["Semua"] + list(db.KATEGORI_BUKU.keys()), key="trx_kat_buku", label_visibility="collapsed")
    
    c5.markdown("**Waktu**")
    c5.selectbox("Urut Waktu", ["Terbaru", "Terlama"], key="trx_sort_waktu", label_visibility="collapsed")
    
    c6.markdown("**Status**")
    f_status = c6.selectbox("Filter Status", ["Semua", "DIPINJAM", "TERLAMBAT", "DIKEMBALIKAN"], key="trx_filter_status", label_visibility="collapsed")
    c7.markdown("**Aksi**")

    sql = """SELECT t.*, p.nama AS nama_peminjam, b.kode_buku, b.judul, b.kategori FROM transaksi t 
             JOIN peminjam p ON p.id=t.id_peminjam JOIN buku b ON b.id=t.id_buku WHERE 1=1"""
    params = []

    if cari:
        sql += """ AND (t.kode_transaksi LIKE ? OR p.nama LIKE ? OR p.nim LIKE ?
                  OR b.kode_buku LIKE ? OR b.judul LIKE ?)"""
        params.extend([f"%{cari}%"] * 5)
             
    if f_status != "Semua": sql += f" AND t.status = '{f_status}'"
    if st.session_state["trx_kat_buku"] != "Semua": sql += f" AND b.kategori = '{st.session_state['trx_kat_buku']}'"
    
    if st.session_state["trx_sort_nama"] == "A-Z": sql += " ORDER BY p.nama ASC"
    else: sql += " ORDER BY p.nama DESC"
    
    if st.session_state["trx_sort_waktu"] == "Terbaru": sql += ", t.id DESC"
    else: sql += ", t.id ASC"
        
    data = db.q(sql, params)
    if not data:
        st.info("Tidak ada transaksi yang cocok dengan pencarian atau filter.")
        return

    for t in data:
        hari, denda = db.hitung_denda(t)
        c1, c2, c3, c4, c5, c6, c7 = st.columns([1.5, 2, 2.5, 1.8, 2, 1.8, 1.5])
        
        if c1.button(t["kode_transaksi"], key=f"tbl_id_{t['id']}", icon=":material/visibility:", use_container_width=True):
            buka_detail_trx(t["id"]); st.rerun()
        c2.write(t["nama_peminjam"])
        c3.write(t["judul"])
        c4.write(t["kategori"])
        
        info_waktu = f"Pinjam: {tgl(t['tanggal_pinjam'])}<br>Tenggat: {tgl(t['tanggal_tenggat'])}"
        c5.markdown(f"<span style='font-size:12px'>{info_waktu}</span>", unsafe_allow_html=True)
        c6.markdown(badge(t["status"]), unsafe_allow_html=True)

        with c7:
            if t["status"] == "TERLAMBAT":
                p = db.q("SELECT * FROM peminjam WHERE id=?", (t["id_peminjam"],))[0]
                b = db.q("SELECT * FROM buku WHERE id=?", (t["id_buku"],))[0]
                pesan_wa = wa_terlambat(t, p, b, hari, denda)
                url_wa = url_whatsapp(p["no_whatsapp"], pesan_wa)
                if url_wa:
                    st.link_button(
                        "Kirim WhatsApp", url_wa, key=f"wa_trx_{t['id']}",
                        icon=":material/send:", use_container_width=True,
                    )
                else:
                    st.warning("Nomor WhatsApp tidak valid.")
            else:
                st.write("-")

# ================= HALAMAN: PEMINJAMAN =================
def halaman_peminjaman():
    st.title("Peminjaman Buku")
    
    nim_input = st.text_input("Masukkan NIM Anggota", placeholder="Contoh: 20240001")
    if not nim_input: 
        st.info("Silakan masukkan NIM untuk memulai.")
        return
        
    pem = db.q("SELECT * FROM peminjam WHERE nim=?", (nim_input.strip(),))
    if not pem: 
        st.error("Pencarian gagal: anggota dengan NIM tersebut tidak ditemukan.")
        return
    pem = pem[0]
    
    boleh, alasan = db.cek_boleh_pinjam(pem["id"])
    if not boleh: 
        st.warning(alasan)
        transaksi_aktif = db.q(
            """SELECT t.id, t.kode_transaksi, t.status, t.tanggal_pinjam,
                      t.tanggal_tenggat, b.judul
               FROM transaksi t JOIN buku b ON b.id=t.id_buku
               WHERE t.id_peminjam=? AND t.status IN ('DIPINJAM','TERLAMBAT')
               ORDER BY t.id DESC""",
            (pem["id"],),
        )
        if transaksi_aktif:
            st.subheader("Transaksi yang belum selesai")
            for transaksi in transaksi_aktif:
                c1, c2 = st.columns([4, 1])
                c1.write(
                    f"**{transaksi['kode_transaksi']}** — {transaksi['judul']} | "
                    f"Pinjam {tgl(transaksi['tanggal_pinjam'])} | "
                    f"Tenggat {tgl(transaksi['tanggal_tenggat'])}"
                )
                c2.markdown(badge(transaksi["status"]), unsafe_allow_html=True)
                if st.button(
                    "Lihat detail dan bukti transaksi",
                    key=f"pinjam_detail_aktif_{transaksi['id']}",
                    icon=":material/receipt_long:",
                ):
                    buka_detail_trx(transaksi["id"])
                    st.rerun()
        return
        
    st.success(f"Data ditemukan: **{pem['nama']}** | {pem['program_studi']} | Kelas {pem['kelas']}")
    
    st.markdown("---")
    st.write("**Konfirmasi Nomor WhatsApp**")
    c_wa1, c_wa2 = st.columns([2, 3])
    with c_wa1: st.info(f"No. Terdaftar: **{pem['no_whatsapp']}**")
    with c_wa2: wa_baru = st.text_input("Ganti No. WA (Opsional)", value=pem["no_whatsapp"])
    
    no_wa_final = wa_baru.strip()
    if not db.validasi_wa(no_wa_final): 
        st.error("Format WhatsApp tidak valid. Nomor harus diawali 62 dan terdiri dari 8-13 digit.")
        return
    
    st.markdown("---")
    st.write("**Pilih Buku yang Akan Dipinjam**")
    
    buku_tersedia = db.q("SELECT * FROM buku WHERE stok_tersedia > 0 ORDER BY judul")
    if not buku_tersedia:
        st.error("Tidak ada buku yang tersedia saat ini.")
        return
        
    opsi_buku = {f"{b['kode_buku']} - {b['judul']}": b for b in buku_tersedia}
    pilihan = st.selectbox("Daftar Buku Tersedia", list(opsi_buku.keys()))
    buk = opsi_buku[pilihan]
    
    st.markdown("---")
    setting = db.pengaturan()
    hari_ini = date.today()
    tenggat = hari_ini + timedelta(days=setting["lama_pinjam"])
    st.info(f"Tanggal Pinjam: **{tgl(hari_ini)}** | Batas Pengembalian: **{tgl(tenggat)}** ({setting['lama_pinjam']} Hari)")
    
    if st.button("Proses Peminjaman", type="primary", icon=":material/bookmark_add:"):
        try:
            id_trx, kode, tenggat = db.proses_peminjaman(
                pem["id"], buk["id"], no_wa_final, hari_ini
            )
        except (ValueError, sqlite3.Error) as error:
            st.error(f"Peminjaman gagal diproses: {error}")
        else:
            pem["no_whatsapp"] = no_wa_final
            transaksi = ambil_transaksi_lengkap(id_trx)
            pesan = wa_sukses_pinjam(
                transaksi, pem, buk, tgl(tenggat)
            )
            kirim_dan_catat_whatsapp(id_trx, "BUKTI", pem["no_whatsapp"], pesan)
            flash(f"Peminjaman {kode} berhasil diproses. Detail dan bukti transaksi tersedia.")
            buka_detail_trx(id_trx)
            st.rerun()

# ================= HALAMAN: PENGEMBALIAN =================
def halaman_pengembalian():
    st.title("Pengembalian Buku")
    
    cari = st.text_input("Masukkan NIM atau ID Transaksi", placeholder="Cari NIM (cth: 20240001) atau ID (cth: TRX-...)")
    if not cari: 
        st.info("Silakan masukkan NIM atau ID Transaksi untuk mencari data pinjaman.")
        return

    rows = db.q("""SELECT t.*, p.nim, p.nama AS nama_peminjam, p.no_whatsapp, b.kode_buku, b.judul 
                   FROM transaksi t JOIN peminjam p ON p.id=t.id_peminjam JOIN buku b ON b.id=t.id_buku
                   WHERE t.status IN ('DIPINJAM','TERLAMBAT') AND (p.nim LIKE ? OR t.kode_transaksi LIKE ?)""",
                (f"%{cari}%", f"%{cari}%"))

    if not rows: 
        st.warning("Tidak ada pinjaman aktif yang sesuai dengan NIM atau ID transaksi. Periksa kembali data pencarian.")
        return

    for t in rows:
        hari, denda = db.hitung_denda(t)
        with st.expander(f"Transaksi {t['kode_transaksi']} - {t['nama_peminjam']}", expanded=True):
            st.write(f"**Buku:** {t['judul']} ({t['kode_buku']})")
            st.write(f"**Batas Waktu:** {tgl(t['tanggal_tenggat'])}")
            st.markdown(badge(t["status"]), unsafe_allow_html=True)
            
            if hari > 0: 
                st.error(f"Terlambat {hari} hari. Denda: **{rp(denda)}**")
            else: 
                st.success("Tidak ada denda.")

            if st.button("Proses Pengembalian & Lunas Denda", key=f"kembali_{t['id']}", type="primary", icon=":material/assignment_return:"):
                try:
                    hari, denda = db.proses_pengembalian(t["id"], date.today())
                except (ValueError, sqlite3.Error) as error:
                    st.error(f"Pengembalian gagal diproses: {error}")
                else:
                    transaksi = ambil_transaksi_lengkap(t["id"])
                    peminjam = {
                        "nama": transaksi["nama_peminjam"],
                    }
                    buku = {"judul": transaksi["judul"]}
                    pesan = wa_sukses_kembali(transaksi, peminjam, buku, denda)
                    kirim_dan_catat_whatsapp(
                        t["id"], "PENGEMBALIAN", transaksi["no_whatsapp"], pesan
                    )
                    flash(
                        f"Pengembalian {transaksi['kode_transaksi']} berhasil. "
                        "Stok buku dipulihkan dan detail transaksi tersedia."
                    )
                    buka_detail_trx(t["id"])
                    st.rerun()

# ================= HALAMAN: DENDA =================
def halaman_denda():
    st.title("Denda")
    
    c1, c2, c3 = st.columns([4, 4, 2])
    c1.markdown("**Nama Peminjam**")
    c1.selectbox("Urut Nama", ["A-Z", "Z-A"], key="denda_sort_nama", label_visibility="collapsed")
    c2.markdown("**Status Denda**")
    c2.markdown("Lunas")
    c3.markdown("**Total**")

    urutan = "ASC" if st.session_state["denda_sort_nama"] == "A-Z" else "DESC"
    rows = db.q(
        """SELECT t.*, p.nama AS nama_peminjam, b.judul, b.kode_buku
           FROM transaksi t
           JOIN peminjam p ON p.id=t.id_peminjam
           JOIN buku b ON b.id=t.id_buku
           WHERE t.status='DIKEMBALIKAN' AND t.denda_lunas=1 AND t.total_denda>0
           ORDER BY p.nama """ + urutan + ", t.id DESC"
    )
    denda_rows = []
    for transaksi in rows:
        hari, total = db.hitung_denda(transaksi)
        if total <= 0:
            continue
        transaksi["hari_denda"] = hari
        transaksi["jumlah_denda"] = total
        denda_rows.append(transaksi)

    if not denda_rows:
        st.success("Tidak ada catatan denda.")
        return

    st.metric("Total Denda Lunas", rp(sum(t["jumlah_denda"] for t in denda_rows)))
    for t in denda_rows:
        nama, status, total = st.columns([4, 4, 2])
        nama.write(t["nama_peminjam"])
        status.write("LUNAS")
        total.write(rp(t["jumlah_denda"]))
        with st.expander(f"Detail denda — {t['kode_transaksi']}"):
            st.write(f"**Buku:** {t['judul']} ({t['kode_buku']})")
            st.write(f"**Keterlambatan:** {t['hari_denda']} hari")
            st.write(f"**Total denda:** {rp(t['jumlah_denda'])}")

# ================= HALAMAN: MASTER DATA =================
def halaman_master_buku():
    st.title("Data Buku")
    
    if st.button("Tambah Buku", type="primary", icon=":material/add:"):
        st.session_state["mode_edit_buku"] = None
        st.session_state["mode_tambah"] = "buku"
        
    if st.session_state.get("mode_tambah") == "buku":
        kategori_buku = list(db.KATEGORI_BUKU)
        st.session_state.setdefault("buku_kategori_baru", kategori_buku[0])
        st.write("**Pilih Kategori Buku**")
        kategori_cols = st.columns(4)
        for index, kategori in enumerate(kategori_buku):
            if kategori_cols[index % 4].button(
                kategori,
                key=f"pilih_kategori_buku_{index}",
                type=(
                    "primary"
                    if st.session_state["buku_kategori_baru"] == kategori
                    else "secondary"
                ),
                use_container_width=True,
            ):
                st.session_state["buku_kategori_baru"] = kategori
        kat = st.session_state["buku_kategori_baru"]
        st.caption(f"Kode buku berikutnya: {db.generate_kode_buku(kat)}")

        with st.form("form_tambah_buku"):
            st.subheader("Form Tambah Buku")
            c1, c2 = st.columns(2)
            judul = c1.text_input("Judul Buku", placeholder="Cth: Belajar Python")
            isbn = c2.text_input("ISBN", placeholder="Cth: 978602...")
            tahun = c2.number_input("Tahun Terbit", 1900, 2100, 2024)
            
            penulis_all = db.q("SELECT * FROM penulis ORDER BY nama_penulis")
            penerbit_all = db.q("SELECT * FROM penerbit ORDER BY nama_penerbit")
            
            id_pn = c1.selectbox("Penulis", [p["id"] for p in penulis_all], format_func=lambda x: next(p["nama_penulis"] for p in penulis_all if p["id"]==x))
            id_pb = c2.selectbox("Penerbit", [p["id"] for p in penerbit_all], format_func=lambda x: next(p["nama_penerbit"] for p in penerbit_all if p["id"]==x))
            
            stok = st.number_input("Total Stok", 1, 1000, 1)
            
            if st.form_submit_button("Simpan Buku", icon=":material/save:"):
                if not judul or not isbn:
                    st.error("Judul dan ISBN wajib diisi.")
                else:
                    kode_otomatis = db.generate_kode_buku(kat)
                    try:
                        db.x("""INSERT INTO buku (kode_buku,isbn,judul,kategori,id_penulis,id_penerbit,tahun_terbit,stok_total,stok_tersedia)
                                VALUES (?,?,?,?,?,?,?,?,?)""", (kode_otomatis, isbn, judul, kat, id_pn, id_pb, tahun, stok, stok))
                        st.success(f"Buku {kode_otomatis} berhasil ditambahkan.")
                        st.session_state["mode_tambah"] = False
                        st.rerun()
                    except Exception as e:
                        st.error(f"Gagal menambahkan buku: {e}")
        if st.button("Batal", icon=":material/close:"):
            st.session_state["mode_tambah"] = False; st.rerun()
        st.markdown("---")

    edit_id = st.session_state.get("mode_edit_buku")
    if edit_id is not None:
        buku_edit = db.q("SELECT * FROM buku WHERE id=?", (edit_id,))
        if not buku_edit:
            st.session_state["mode_edit_buku"] = None
            st.error("Buku yang akan diedit tidak ditemukan.")
        else:
            buku_edit = buku_edit[0]
            penulis_all = db.q("SELECT * FROM penulis ORDER BY nama_penulis")
            penerbit_all = db.q("SELECT * FROM penerbit ORDER BY nama_penerbit")
            penulis_ids = [p["id"] for p in penulis_all]
            penerbit_ids = [p["id"] for p in penerbit_all]
            with st.form(f"form_edit_buku_{edit_id}"):
                st.subheader(f"Edit Buku: {buku_edit['judul']}")
                c1, c2 = st.columns(2)
                judul_edit = c1.text_input("Judul Buku", value=buku_edit["judul"])
                isbn_edit = c2.text_input("ISBN", value=buku_edit["isbn"])
                kategori_edit = c1.selectbox(
                    "Kategori Buku", list(db.KATEGORI_BUKU.keys()),
                    index=list(db.KATEGORI_BUKU.keys()).index(buku_edit["kategori"]),
                )
                tahun_edit = c2.number_input(
                    "Tahun Terbit", 0, 2100, int(buku_edit["tahun_terbit"]),
                )
                id_penulis_edit = c1.selectbox(
                    "Penulis", penulis_ids,
                    index=penulis_ids.index(buku_edit["id_penulis"]),
                    format_func=lambda value: next(p["nama_penulis"] for p in penulis_all if p["id"] == value),
                )
                id_penerbit_edit = c2.selectbox(
                    "Penerbit", penerbit_ids,
                    index=penerbit_ids.index(buku_edit["id_penerbit"]),
                    format_func=lambda value: next(p["nama_penerbit"] for p in penerbit_all if p["id"] == value),
                )
                stok_edit = st.number_input("Total Stok", 1, 1000, int(buku_edit["stok_total"]))
                simpan_edit = st.form_submit_button("Lanjutkan", icon=":material/arrow_forward:")
                if simpan_edit:
                    dipinjam = buku_edit["stok_total"] - buku_edit["stok_tersedia"]
                    if not judul_edit.strip() or not isbn_edit.strip():
                        st.error("Judul dan ISBN wajib diisi.")
                    elif stok_edit < dipinjam:
                        st.error(f"Total stok tidak boleh kurang dari {dipinjam} karena buku sedang dipinjam.")
                    else:
                        st.session_state["konfirmasi_buku"] = {
                            "aksi": "edit",
                            "id": edit_id,
                            "data": {
                                "judul": judul_edit.strip(),
                                "isbn": isbn_edit.strip(),
                                "kategori": kategori_edit,
                                "tahun_terbit": tahun_edit,
                                "id_penulis": id_penulis_edit,
                                "id_penerbit": id_penerbit_edit,
                                "stok_total": stok_edit,
                                "stok_tersedia": buku_edit["stok_tersedia"] + stok_edit - buku_edit["stok_total"],
                            },
                        }
                        st.rerun()
            if st.button("Batal Edit", key="batal_edit_buku", icon=":material/close:"):
                st.session_state["mode_edit_buku"] = None
                st.rerun()
            st.markdown("---")

    konfirmasi = st.session_state.get("konfirmasi_buku")
    if konfirmasi:
        judul_konfirmasi = (
            "Simpan perubahan buku ini?"
            if konfirmasi["aksi"] == "edit"
            else "Hapus buku ini?"
        )
        pesan_konfirmasi = (
            "Apakah Anda yakin ingin menyimpan perubahan data buku?"
            if konfirmasi["aksi"] == "edit"
            else "Apakah Anda yakin ingin menghapus data buku ini? Tindakan ini tidak dapat dibatalkan."
        )
        with st.container(border=True):
            st.warning(f"**{judul_konfirmasi}** {pesan_konfirmasi}")
            ya, batal = st.columns(2)
            if ya.button("Ya, Lanjutkan", key="konfirmasi_buku_ya", type="primary", icon=":material/check:"):
                if konfirmasi["aksi"] == "edit":
                    data_edit = konfirmasi["data"]
                    try:
                        db.x(
                            """UPDATE buku SET isbn=?, judul=?, kategori=?, id_penulis=?, id_penerbit=?,
                               tahun_terbit=?, stok_total=?, stok_tersedia=? WHERE id=?""",
                            (
                                data_edit["isbn"], data_edit["judul"], data_edit["kategori"],
                                data_edit["id_penulis"], data_edit["id_penerbit"], data_edit["tahun_terbit"],
                                data_edit["stok_total"], data_edit["stok_tersedia"], konfirmasi["id"],
                            ),
                        )
                    except sqlite3.IntegrityError:
                        st.error("Perubahan gagal. ISBN tersebut mungkin sudah digunakan oleh buku lain.")
                    else:
                        st.session_state["mode_edit_buku"] = None
                        st.session_state["konfirmasi_buku"] = None
                        flash("Perubahan data buku berhasil disimpan.")
                        st.rerun()
                else:
                    pakai = db.q(
                        "SELECT COUNT(*) n FROM transaksi WHERE id_buku=?",
                        (konfirmasi["id"],),
                    )[0]["n"]
                    if pakai:
                        st.session_state["konfirmasi_buku"] = None
                        flash("Buku tidak dapat dihapus karena sudah tercatat dalam riwayat transaksi.", "error")
                        st.rerun()
                    db.x("DELETE FROM buku WHERE id=?", (konfirmasi["id"],))
                    st.session_state["konfirmasi_buku"] = None
                    flash("Data buku berhasil dihapus.")
                    st.rerun()
            if batal.button("Batal", key="konfirmasi_buku_batal", icon=":material/close:"):
                st.session_state["konfirmasi_buku"] = None
                st.rerun()

    penulis_all = db.q("SELECT * FROM penulis ORDER BY nama_penulis")
    penerbit_all = db.q("SELECT * FROM penerbit ORDER BY nama_penerbit")
    kolom = st.columns([1.5, 2.2, 1.5, 1.7, 1.7, 1.2, 1.5])
    kolom[0].markdown("**Kode Buku**")
    kolom[0].selectbox("Urut Kode", ["A-Z", "Z-A"], key="buku_sort_kode", label_visibility="collapsed")
    kolom[1].markdown("**Judul Buku**")
    kolom[1].selectbox("Urut Judul", ["A-Z", "Z-A"], key="buku_sort_judul", label_visibility="collapsed")
    kolom[2].markdown("**Kategori**")
    kolom[2].selectbox(
        "Filter Kategori", ["Semua"] + list(db.KATEGORI_BUKU.keys()),
        key="buku_filter_kat", label_visibility="collapsed",
    )
    kolom[3].markdown("**Penulis**")
    kolom[3].selectbox(
        "Filter Penulis", ["Semua"] + [p["nama_penulis"] for p in penulis_all],
        key="buku_filter_penulis", label_visibility="collapsed",
    )
    kolom[4].markdown("**Penerbit**")
    kolom[4].selectbox(
        "Filter Penerbit", ["Semua"] + [p["nama_penerbit"] for p in penerbit_all],
        key="buku_filter_penerbit", label_visibility="collapsed",
    )
    kolom[5].markdown("**Stok**")
    kolom[5].selectbox(
        "Filter Stok", ["Semua", "Tersedia", "Habis"],
        key="buku_filter_stok", label_visibility="collapsed",
    )
    kolom[6].markdown("**Aksi**")
    st.markdown("<hr style='margin:0; border:1px solid #e5e7eb'>", unsafe_allow_html=True)

    sql = """SELECT b.*, pn.nama_penulis, pb.nama_penerbit FROM buku b
             JOIN penulis pn ON pn.id=b.id_penulis JOIN penerbit pb ON pb.id=b.id_penerbit WHERE 1=1"""
    params = []
    for key, column in (
        ("buku_filter_kat", "b.kategori"),
        ("buku_filter_penulis", "pn.nama_penulis"),
        ("buku_filter_penerbit", "pb.nama_penerbit"),
    ):
        value = st.session_state[key]
        if value != "Semua":
            sql += f" AND {column}=?"
            params.append(value)
    if st.session_state["buku_filter_stok"] == "Tersedia":
        sql += " AND b.stok_tersedia > 0"
    elif st.session_state["buku_filter_stok"] == "Habis":
        sql += " AND b.stok_tersedia <= 0"
    order_kode = "ASC" if st.session_state["buku_sort_kode"] == "A-Z" else "DESC"
    order_judul = "ASC" if st.session_state["buku_sort_judul"] == "A-Z" else "DESC"
    sql += f" ORDER BY b.kode_buku {order_kode}, b.judul {order_judul}"
    data = db.q(sql, params)

    for b in data:
        baris = st.columns([1.5, 2.2, 1.5, 1.7, 1.7, 1.2, 1.5])
        baris[0].write(b["kode_buku"])
        baris[1].write(b["judul"])
        baris[2].write(b["kategori"])
        baris[3].write(b["nama_penulis"])
        baris[4].write(b["nama_penerbit"])
        baris[5].write(f"{b['stok_tersedia']} / {b['stok_total']}")
        with baris[6]:
            edit, hapus = st.columns(2)
            if edit.button("Edit", key=f"eb_{b['id']}", icon=":material/edit:"):
                st.session_state["mode_tambah"] = False
                st.session_state["mode_edit_buku"] = b["id"]
                st.rerun()
            if hapus.button("Hapus", key=f"hb_{b['id']}", icon=":material/delete:"):
                st.session_state["konfirmasi_buku"] = {"aksi": "hapus", "id": b["id"]}
                st.rerun()

def halaman_master_anggota():
    st.title("Data Anggota")
    
    if st.button("Tambah Anggota", type="primary", icon=":material/person_add:"):
        st.session_state["mode_edit_peminjam"] = None
        st.session_state["mode_tambah"] = "anggota"
        
    if st.session_state.get("mode_tambah") == "anggota":
        with st.form("form_tambah_anggota"):
            st.subheader("Form Tambah Anggota")
            
            nim = st.text_input("NIM (Angka saja)", placeholder="Contoh: 20241001")
            nama = st.text_input("Nama Lengkap", placeholder="Contoh: Kylie Jenner")
            wa = st.text_input("No WhatsApp (Awali dengan 62)", placeholder="Contoh: 6281234567890")
            
            jurusan_options = list(db.DATA_JURUSAN_PRODI_KELAS)
            jur = st.selectbox("Pilih Jurusan", jurusan_options)
            list_prodi = db.DATA_JURUSAN_PRODI_KELAS[jur]
            prodi_opt = sorted({prodi for prodi, _ in list_prodi})
            prodi = st.selectbox("Pilih Program Studi", prodi_opt)
            kelas_options = sorted({
                kelas for nama_prodi, kelas in list_prodi if nama_prodi == prodi
            })
            kelas = st.selectbox("Pilih Kelas", kelas_options)
            
            if st.form_submit_button("Simpan Anggota", icon=":material/save:"):
                if not db.validasi_nim(nim):
                    st.error("Format NIM tidak valid. NIM hanya boleh berisi angka.")
                elif not db.validasi_wa(wa):
                    st.error("Format WhatsApp tidak valid. Nomor harus diawali 62 dan terdiri dari 8-13 digit.")
                else:
                    try:
                        db.x("INSERT INTO peminjam (nim,nama,program_studi,jurusan,kelas,no_whatsapp) VALUES (?,?,?,?,?,?)", (nim, nama, prodi, jur, kelas, wa))
                        st.success("Anggota berhasil ditambahkan.")
                        st.session_state["mode_tambah"] = False
                        st.rerun()
                    except Exception as e: 
                        st.error(f"Gagal menambahkan anggota. NIM mungkin sudah terdaftar: {e}")
        if st.button("Batal", icon=":material/close:"):
            st.session_state["mode_tambah"] = False; st.rerun()
        st.markdown("---")

    edit_id = st.session_state.get("mode_edit_peminjam")
    if edit_id is not None:
        rows = db.q("SELECT * FROM peminjam WHERE id=?", (edit_id,))
        if not rows:
            st.session_state["mode_edit_peminjam"] = None
            st.error("Anggota yang akan diedit tidak ditemukan.")
        else:
            anggota_edit = rows[0]
            with st.form(f"form_edit_peminjam_{edit_id}"):
                st.subheader(f"Edit Anggota: {anggota_edit['nama']}")
                c1, c2 = st.columns(2)
                nim_edit = c1.text_input("NIM (Angka saja)", value=anggota_edit["nim"])
                nama_edit = c2.text_input("Nama Lengkap", value=anggota_edit["nama"])
                jurusan_options = list(db.DATA_JURUSAN_PRODI_KELAS)
                if anggota_edit["jurusan"] not in jurusan_options:
                    jurusan_options.append(anggota_edit["jurusan"])
                jurusan_edit = st.selectbox(
                    "Pilih Jurusan", jurusan_options,
                    index=jurusan_options.index(anggota_edit["jurusan"]),
                    key=f"edit_pem_jurusan_{edit_id}",
                )
                prodi_data = db.DATA_JURUSAN_PRODI_KELAS.get(jurusan_edit, [])
                prodi_labels = sorted({prodi for prodi, _ in prodi_data})
                if anggota_edit["program_studi"] not in prodi_labels:
                    prodi_labels.append(anggota_edit["program_studi"])
                prodi_labels.sort()
                prodi_edit = st.selectbox(
                    "Pilih Program Studi", prodi_labels,
                    index=prodi_labels.index(anggota_edit["program_studi"]),
                    key=f"edit_pem_prodi_{edit_id}",
                )
                kelas_options = sorted({
                    kelas for prodi, kelas in prodi_data if prodi == prodi_edit
                })
                if anggota_edit["kelas"] not in kelas_options:
                    kelas_options.append(anggota_edit["kelas"])
                    kelas_options.sort()
                kelas_edit = st.selectbox(
                    "Pilih Kelas", kelas_options,
                    index=kelas_options.index(anggota_edit["kelas"]),
                    key=f"edit_pem_kelas_{edit_id}",
                )
                wa_edit = st.text_input(
                    "No WhatsApp (Awali dengan 62)", value=anggota_edit["no_whatsapp"],
                )
                if st.form_submit_button("Lanjutkan", icon=":material/arrow_forward:"):
                    if not nim_edit.strip() or (
                        nim_edit.strip() != anggota_edit["nim"]
                        and not db.validasi_nim(nim_edit.strip())
                    ):
                        st.error("Format NIM tidak valid. NIM baru hanya boleh berisi angka.")
                    elif not nama_edit.strip():
                        st.error("Nama lengkap wajib diisi.")
                    elif not db.validasi_wa(wa_edit.strip()):
                        st.error("Format WhatsApp tidak valid. Nomor harus diawali 62 dan berisi 8-13 digit setelahnya.")
                    else:
                        st.session_state["konfirmasi_peminjam"] = {
                            "aksi": "edit",
                            "id": edit_id,
                            "data": {
                                "nim": nim_edit.strip(),
                                "nama": nama_edit.strip(),
                                "program_studi": prodi_edit,
                                "jurusan": jurusan_edit,
                                "kelas": kelas_edit,
                                "no_whatsapp": wa_edit.strip(),
                            },
                        }
                        st.rerun()
            if st.button("Batal Edit", key="batal_edit_peminjam", icon=":material/close:"):
                st.session_state["mode_edit_peminjam"] = None
                st.rerun()
            st.markdown("---")

    konfirmasi = st.session_state.get("konfirmasi_peminjam")
    if konfirmasi:
        if konfirmasi["aksi"] == "edit":
            judul_konfirmasi = "Simpan perubahan data anggota?"
            pesan_konfirmasi = "Apakah Anda yakin ingin menyimpan perubahan data anggota ini?"
        else:
            judul_konfirmasi = "Hapus data anggota?"
            pesan_konfirmasi = "Apakah Anda yakin ingin menghapus anggota ini? Tindakan ini tidak dapat dibatalkan."
        with st.container(border=True):
            st.warning(f"**{judul_konfirmasi}** {pesan_konfirmasi}")
            ya, batal = st.columns(2)
            if ya.button("Ya, Lanjutkan", key="konfirmasi_peminjam_ya", type="primary", icon=":material/check:"):
                if konfirmasi["aksi"] == "edit":
                    anggota_baru = konfirmasi["data"]
                    try:
                        db.x(
                            """UPDATE peminjam SET nim=?, nama=?, program_studi=?, jurusan=?, kelas=?,
                               no_whatsapp=? WHERE id=?""",
                            (
                                anggota_baru["nim"], anggota_baru["nama"], anggota_baru["program_studi"],
                                anggota_baru["jurusan"], anggota_baru["kelas"], anggota_baru["no_whatsapp"],
                                konfirmasi["id"],
                            ),
                        )
                    except sqlite3.IntegrityError:
                        st.error("Perubahan gagal. NIM tersebut mungkin sudah digunakan anggota lain.")
                    else:
                        st.session_state["mode_edit_peminjam"] = None
                        st.session_state["konfirmasi_peminjam"] = None
                        flash("Perubahan data anggota berhasil disimpan.")
                        st.rerun()
                else:
                    pakai = db.q(
                        "SELECT COUNT(*) n FROM transaksi WHERE id_peminjam=?",
                        (konfirmasi["id"],),
                    )[0]["n"]
                    if pakai:
                        st.session_state["konfirmasi_peminjam"] = None
                        flash("Anggota tidak dapat dihapus karena sudah tercatat dalam riwayat transaksi.", "error")
                        st.rerun()
                    db.x("DELETE FROM peminjam WHERE id=?", (konfirmasi["id"],))
                    st.session_state["konfirmasi_peminjam"] = None
                    flash("Data anggota berhasil dihapus.")
                    st.rerun()
            if batal.button("Batal", key="konfirmasi_peminjam_batal", icon=":material/close:"):
                st.session_state["konfirmasi_peminjam"] = None
                st.rerun()

    jurusan_options = list(db.DATA_JURUSAN_PRODI_KELAS.keys())
    prodi_options = sorted({
        prodi for daftar in db.DATA_JURUSAN_PRODI_KELAS.values() for prodi, _ in daftar
    })
    kelas_options = sorted({
        kelas for daftar in db.DATA_JURUSAN_PRODI_KELAS.values() for _, kelas in daftar
    })
    filter_cols = st.columns([1.3, 1.5, 1.8, 2, 1.2, 1.6, 1.2])
    filter_cols[0].markdown("**NIM**")
    filter_cols[0].selectbox(
        "Urut NIM", ["A-Z", "Z-A"], key="pem_sort_nim", label_visibility="collapsed",
    )
    filter_cols[1].markdown("**Nama**")
    filter_cols[1].selectbox(
        "Urut Nama", ["A-Z", "Z-A"], key="pem_sort_nama", label_visibility="collapsed",
    )
    filter_cols[2].markdown("**Jurusan**")
    filter_cols[2].selectbox(
        "Filter Jurusan", ["Semua"] + jurusan_options,
        key="pem_filter_jur", label_visibility="collapsed",
    )
    filter_cols[3].markdown("**Program Studi**")
    filter_cols[3].selectbox(
        "Filter Program Studi", ["Semua"] + prodi_options,
        key="pem_filter_prodi", label_visibility="collapsed",
    )
    filter_cols[4].markdown("**Kelas**")
    filter_cols[4].selectbox(
        "Filter Kelas", ["Semua"] + kelas_options,
        key="pem_filter_kelas", label_visibility="collapsed",
    )
    filter_cols[5].markdown("**WhatsApp**")
    filter_cols[5].text_input(
        "Cari WhatsApp", placeholder="Cari nomor", key="pem_filter_wa",
        label_visibility="collapsed",
    )
    filter_cols[6].markdown("**Aksi**")

    sql = "SELECT * FROM peminjam WHERE 1=1"
    params = []
    for key, column in (
        ("pem_filter_jur", "jurusan"),
        ("pem_filter_prodi", "program_studi"),
        ("pem_filter_kelas", "kelas"),
    ):
        value = st.session_state[key]
        if value != "Semua":
            sql += f" AND {column}=?"
            params.append(value)
    wa_filter = st.session_state["pem_filter_wa"].strip()
    if wa_filter:
        sql += " AND no_whatsapp LIKE ?"
        params.append(f"%{wa_filter}%")
    arah_nim = "ASC" if st.session_state["pem_sort_nim"] == "A-Z" else "DESC"
    arah_nama = "ASC" if st.session_state["pem_sort_nama"] == "A-Z" else "DESC"
    sql += f" ORDER BY nim {arah_nim}, nama {arah_nama}"
    data = db.q(sql, params)

    header = st.columns([1.3, 1.5, 1.8, 2, 1.2, 1.6, 1.2])
    for col, label in zip(header, ("NIM", "Nama", "Jurusan", "Program Studi", "Kelas", "WhatsApp", "Aksi")):
        col.markdown(f"**{label}**")
    st.markdown("<hr style='margin:0; border:1px solid #e5e7eb'>", unsafe_allow_html=True)

    for p in data:
        baris = st.columns([1.3, 1.5, 1.8, 2, 1.2, 1.6, 1.2])
        baris[0].write(p["nim"])
        baris[1].write(p["nama"])
        baris[2].write(p["jurusan"])
        baris[3].write(p["program_studi"])
        baris[4].write(p["kelas"])
        baris[5].write(p["no_whatsapp"] or "-")
        with baris[6]:
            edit, hapus = st.columns(2)
            if edit.button("Edit", key=f"ep_{p['id']}", icon=":material/edit:"):
                st.session_state["mode_tambah"] = False
                st.session_state["mode_edit_peminjam"] = p["id"]
                st.rerun()
            if hapus.button("Hapus", key=f"hp_{p['id']}", icon=":material/delete:"):
                st.session_state["konfirmasi_peminjam"] = {"aksi": "hapus", "id": p["id"]}
                st.rerun()

def halaman_master_penulis():
    st.title("Data Penulis")
    if st.button("Tambah Penulis", type="primary", icon=":material/add:"):
        st.session_state["mode_tambah"] = "penulis"
        
    if st.session_state.get("mode_tambah") == "penulis":
        with st.form("f_penulis"):
            nama = st.text_input("Nama Penulis", placeholder="Cth: Andrea Hirata")
            if st.form_submit_button("Tambah", icon=":material/add:") and nama.strip():
                try:
                    db.x("INSERT INTO penulis (nama_penulis) VALUES (?)", (nama.strip(),)); st.success("Penulis berhasil ditambahkan."); st.session_state["mode_tambah"] = False; st.rerun()
                except Exception: st.error("Penulis sudah terdaftar.")
        if st.button("Batal", icon=":material/close:"): st.session_state["mode_tambah"] = False; st.rerun()
        st.markdown("---")

    c1, c2 = st.columns([5, 2])
    c1.markdown("**Nama Penulis**")
    c1.selectbox("Urut Nama", ["A-Z", "Z-A"], key="pn_sort", label_visibility="collapsed")
    c2.markdown("**Aksi**")
    
    sql = "SELECT p.*, (SELECT COUNT(*) FROM buku b WHERE b.id_penulis=p.id) as jml_buku FROM penulis p"
    if st.session_state["pn_sort"] == "A-Z": sql += " ORDER BY p.nama_penulis ASC"
    else: sql += " ORDER BY p.nama_penulis DESC"
    
    data = db.q(sql)
    for p in data:
        c1, c2 = st.columns([5, 2])
        c1.write(p['nama_penulis'])
        with c2:
            col1, col2 = st.columns(2)
            if col1.button("Edit", key=f"epn_{p['id']}", icon=":material/edit:"):
                st.session_state["mode_tambah"] = "penulis"
            if col2.button("Hapus", key=f"hpn_{p['id']}", icon=":material/delete:"):
                if p['jml_buku'] > 0: st.error("Tidak dapat menghapus data karena masih terdapat buku terkait.")
                else: db.x("DELETE FROM penulis WHERE id=?", (p["id"],)); st.success("Data penulis berhasil dihapus."); st.rerun()

def halaman_master_penerbit():
    st.title("Data Penerbit")
    if st.button("Tambah Penerbit", type="primary", icon=":material/add:"):
        st.session_state["mode_tambah"] = "penerbit"
        
    if st.session_state.get("mode_tambah") == "penerbit":
        with st.form("f_penerbit"):
            nama = st.text_input("Nama Penerbit", placeholder="Cth: Gramedia")
            if st.form_submit_button("Tambah", icon=":material/add:") and nama.strip():
                try:
                    db.x("INSERT INTO penerbit (nama_penerbit) VALUES (?)", (nama.strip(),)); st.success("Penerbit berhasil ditambahkan."); st.session_state["mode_tambah"] = False; st.rerun()
                except Exception: st.error("Penerbit sudah terdaftar.")
        if st.button("Batal", icon=":material/close:"): st.session_state["mode_tambah"] = False; st.rerun()
        st.markdown("---")

    c1, c2 = st.columns([5, 2])
    c1.markdown("**Nama Penerbit**")
    c1.selectbox("Urut Nama", ["A-Z", "Z-A"], key="pt_sort", label_visibility="collapsed")
    c2.markdown("**Aksi**")
    
    sql = "SELECT pt.*, (SELECT COUNT(*) FROM buku b WHERE b.id_penerbit=pt.id) as jml_buku FROM penerbit pt"
    if st.session_state["pt_sort"] == "A-Z": sql += " ORDER BY pt.nama_penerbit ASC"
    else: sql += " ORDER BY pt.nama_penerbit DESC"
    
    data = db.q(sql)
    for p in data:
        c1, c2 = st.columns([5, 2])
        c1.write(p['nama_penerbit'])
        with c2:
            col1, col2 = st.columns(2)
            if col1.button("Edit", key=f"ept_{p['id']}", icon=":material/edit:"):
                st.session_state["mode_tambah"] = "penerbit"
            if col2.button("Hapus", key=f"hpt_{p['id']}", icon=":material/delete:"):
                if p['jml_buku'] > 0: st.error("Tidak dapat menghapus data karena masih terdapat buku terkait.")
                else: db.x("DELETE FROM penerbit WHERE id=?", (p["id"],)); st.success("Data penerbit berhasil dihapus."); st.rerun()

# ================= DETAIL =================
def tampilkan_detail_transaksi(id_trx):
    st.title("Detail Transaksi")
    if st.button("Kembali", icon=":material/arrow_back:"): tutup_detail(); st.rerun()
    t = ambil_transaksi_lengkap(id_trx)
    if not t: st.error("Data tidak ditemukan."); return

    hari, denda = db.hitung_denda(t)
    
    st.markdown(f"""
    <div style="background:#f8fafc; padding:20px; border-radius:10px; border:1px solid #e2e8f0;">
        <h3 style="margin-top:0; color:#1e293b;">Informasi Transaksi</h3>
        <table style="width:100%; border-collapse:collapse;">
            <tr><td style="padding:5px 0; width:30%; color:#64748b;">Kode Transaksi</td><td><b>{t['kode_transaksi']}</b></td></tr>
            <tr><td style="padding:5px 0; color:#64748b;">Kode Buku</td><td><b>{t['kode_buku']}</b></td></tr>
            <tr><td style="padding:5px 0; color:#64748b;">Judul Buku</td><td><b>{t['judul']}</b></td></tr>
            <tr><td style="padding:5px 0; color:#64748b;">Status</td><td>{badge(t['status'])}</td></tr>
            <tr><td style="padding:5px 0; color:#64748b;">Tanggal Pinjam</td><td>{tgl(t['tanggal_pinjam'])}</td></tr>
            <tr><td style="padding:5px 0; color:#64748b;">Tenggat Pengembalian</td><td>{tgl(t['tanggal_tenggat'])}</td></tr>
            <tr><td style="padding:5px 0; color:#64748b;">Tanggal Kembali</td><td>{tgl(t['tanggal_kembali'])}</td></tr>
            <tr><td style="padding:5px 0; color:#64748b;">Denda</td><td><b style="color:#ef4444;">{rp(denda) if denda > 0 else '-'}</b></td></tr>
        </table>
    </div>
    """, unsafe_allow_html=True)
    
    st.markdown("---")
    st.subheader("Data Peminjam & Buku")
    c1, c2 = st.columns(2)
    with c1:
        st.info(f"**Peminjam:** {t['nama_peminjam']}\n\n**NIM:** {t['nim']}\n\n**Kelas:** {t['kelas']}\n\n**Prodi:** {t['program_studi']}")
    with c2:
        st.success(f"**ISBN:** {t['isbn']}")

    st.markdown("---")
    st.subheader("Riwayat WhatsApp Transaksi")
    log_wa = db.q(
        """SELECT jenis, no_tujuan, isi_pesan, status, waktu_kirim
           FROM log_whatsapp WHERE id_transaksi=? AND status='TERKIRIM' ORDER BY id DESC""",
        (id_trx,),
    )
    for log in log_wa:
        with st.expander(
            f"{log['jenis']} — Terkirim — {log['waktu_kirim']}"
        ):
            st.write(f"**Tujuan:** {log['no_tujuan']}")
            st.text(log["isi_pesan"])

    st.markdown("---")
    st.subheader("Cetak Bukti")
    if st.button("Tampilkan Bukti Transaksi", icon=":material/receipt_long:"):
        st.session_state["_cetak_bukti"] = t["id"]

    if st.session_state.get("_cetak_bukti") == t["id"]:
        status_denda_nota = (
            "LUNAS"
            if t["status"] == "DIKEMBALIKAN"
            else "Dihitung saat pengembalian"
        )
        html_nota = f"""
        <div class='nota-box'>
            <div class='nota-header'>
                <h2>SIPERNAS</h2>
                <p>Jl. Pendidikan No. 1, Kota Kampus<br>Telp: (021) 1234567</p>
            </div>
            <table class='nota-table'>
                <tr><td>No. Transaksi</td><td>: {t['kode_transaksi']}</td></tr>
                <tr><td>Tanggal Cetak</td><td>: {tgl(date.today())}</td></tr>
                <tr><td colspan="2"><hr style="border-top:1px dashed #000; margin:8px 0;"></td></tr>
                <tr><td>Nama Peminjam</td><td>: {t['nama_peminjam']}</td></tr>
                <tr><td>NIM</td><td>: {t['nim']}</td></tr>
                <tr><td>Kelas / Prodi</td><td>: {t['kelas']} / {t['program_studi']}</td></tr>
                <tr><td colspan="2"><hr style="border-top:1px dashed #000; margin:8px 0;"></td></tr>
                <tr><td>Judul Buku</td><td>: {t['judul']}</td></tr>
                <tr><td>Kode Buku</td><td>: {t['kode_buku']}</td></tr>
                <tr><td>Tgl Pinjam</td><td>: {tgl(t['tanggal_pinjam'])}</td></tr>
                <tr><td>Batas Kembali</td><td>: {tgl(t['tanggal_tenggat'])}</td></tr>
                <tr><td>Tgl Dikembalikan</td><td>: {tgl(t['tanggal_kembali'])}</td></tr>
                <tr><td colspan="2"><hr style="border-top:1px dashed #000; margin:8px 0;"></td></tr>
                <tr><td>Denda</td><td>: {rp(denda) if denda > 0 else '-'}</td></tr>
                <tr><td>Status Denda</td><td>: {status_denda_nota}</td></tr>
                <tr><td>Status</td><td>: {t['status']}</td></tr>
            </table>
            <div class='nota-footer'>
                Terima kasih telah menggunakan layanan Perpustakaan.<br>
                Harap simpan bukti ini sebagai tanda peminjaman yang sah.
            </div>
        </div>
        """
        st.markdown(html_nota, unsafe_allow_html=True)
        st.markdown("""<div style="text-align:center; margin-top:20px;"><button class="no-print" onclick="window.print()"
            style="padding:10px 20px; border:none; background:#1e293b; color:#fff;
            border-radius:6px; cursor:pointer; font-weight:bold;">Cetak ke PDF atau Printer</button></div>""", unsafe_allow_html=True)

def tampilkan_detail_peminjam(id_pem):
    st.title("Profil Anggota")
    if st.button("Kembali", icon=":material/arrow_back:"): tutup_detail(); st.rerun()
    p = db.q("SELECT * FROM peminjam WHERE id=?", (id_pem,))[0]
    st.markdown(f"**NIM:** {p['nim']}  \n**Nama:** {p['nama']}  \n**Prodi:** {p['program_studi']}  \n**Jurusan:** {p['jurusan']}  \n**Kelas:** {p['kelas']}  \n**WA:** {p['no_whatsapp']}")
    st.subheader("Riwayat Transaksi")
    rows = db.q("SELECT t.*, b.judul FROM transaksi t JOIN buku b ON b.id=t.id_buku WHERE t.id_peminjam=? ORDER BY t.id DESC", (id_pem,))
    if not rows: st.info("Belum ada riwayat.")
    for t in rows:
        c1, c2, c3 = st.columns([2, 4, 2])
        if c1.button(t["kode_transaksi"], key=f"dp_t_{t['id']}", icon=":material/visibility:"): buka_detail_trx(t["id"]); st.rerun()
        c2.write(f"{t['judul']} — {tgl(t['tanggal_pinjam'])}")
        c3.markdown(badge(t["status"]), unsafe_allow_html=True)

def tampilkan_detail_buku(id_buk):
    st.title("Detail Buku")
    if st.button("Kembali", icon=":material/arrow_back:"): tutup_detail(); st.rerun()
    b = db.q("SELECT b.*, pn.nama_penulis, pb.nama_penerbit FROM buku b JOIN penulis pn ON pn.id=b.id_penulis JOIN penerbit pb ON pb.id=b.id_penerbit WHERE b.id=?", (id_buk,))[0]
    tahun_terbit = b["tahun_terbit"] or "Tidak tercantum"
    st.markdown(f"**{b['kode_buku']} — {b['judul']}**  \nISBN: {b['isbn']} | Penulis: {b['nama_penulis']} | Penerbit: {b['nama_penerbit']} | Tahun terbit: {tahun_terbit}")
    st.markdown(f"**Stok:** {b['stok_tersedia']} tersedia / {b['stok_total']} total")
    st.subheader("Riwayat Peminjaman")
    rows = db.q("SELECT t.*, p.nama AS nama_peminjam, p.nim FROM transaksi t JOIN peminjam p ON p.id=t.id_peminjam WHERE t.id_buku=? ORDER BY t.id DESC", (id_buk,))
    if not rows: st.info("Belum pernah dipinjam."); return
    for t in rows:
        c1, c2, c3 = st.columns([2, 4, 2])
        if c1.button(t["kode_transaksi"], key=f"db_t_{t['id']}", icon=":material/visibility:"): buka_detail_trx(t["id"]); st.rerun()
        c2.write(f"{t['nama_peminjam']} — {tgl(t['tanggal_pinjam'])}")
        c3.markdown(badge(t["status"]), unsafe_allow_html=True)

# ================= ROUTING UTAMA =================
render_sidebar()
tampil_flash()

if st.session_state["detail_trx"]: tampilkan_detail_transaksi(st.session_state["detail_trx"])
elif st.session_state["detail_pem"]: tampilkan_detail_peminjam(st.session_state["detail_pem"])
elif st.session_state["detail_buk"]: tampilkan_detail_buku(st.session_state["detail_buk"])
elif st.session_state["halaman"] == "Dashboard": halaman_dashboard()
elif st.session_state["halaman"] == "Peminjaman": halaman_peminjaman()
elif st.session_state["halaman"] == "Pengembalian": halaman_pengembalian()
elif st.session_state["halaman"] == "Buku": halaman_master_buku()
elif st.session_state["halaman"] == "Anggota": halaman_master_anggota()
elif st.session_state["halaman"] == "Penulis": halaman_master_penulis()
elif st.session_state["halaman"] == "Penerbit": halaman_master_penerbit()
elif st.session_state["halaman"] == "Transaksi": halaman_transaksi()
elif st.session_state["halaman"] == "Denda": halaman_denda()