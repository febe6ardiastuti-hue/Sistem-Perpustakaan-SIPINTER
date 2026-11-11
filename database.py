"""database.py — Skema, data statis (Excel), dan aturan bisnis SIPINTER."""
import hashlib
import sqlite3
import re
from pathlib import Path
from datetime import date, datetime, timedelta

import pandas as pd

DB_PATH = Path(__file__).resolve().parent / "perpustakaan.db"

# ================= DATA STATIS (DARI EXCEL) =================
DATA_JURUSAN_PRODI_KELAS = {}

KATEGORI_BUKU = {
    "Fiksi & Fantasi": "FF", "Fiksi & Romansa": "FR", "Sastra Indonesia": "SI",
    "Fiksi & Sastra": "FS", "Fiksi Remaja": "FRe", "Sejarah & Budaya": "SB",
    "Sains Anak": "SA", "Pengembangan Diri": "PD", "Bisnis & Investasi": "BI",
    "Dongeng Anak": "DA", "Aktivitas Anak": "AA", "Komik & Manga": "KM",
    "Edukasi Anak": "EA", "Kuliner": "KL", "Fiksi & Thriller": "FT",
    "Fiksi Sejarah": "FSe", "Musik": "MU", "Cerita Pendek": "CP",
    "Fiksi & Keluarga": "FK", "Perjalanan & Esai": "PE", "Biografi": "BIO",
    "Horor": "HOR", "Buku Anak": "BA", "Fiksi & Petualangan": "FP",
    "Fiksi & Pendidikan": "FPe", "Fiksi Anak & Keluarga": "FAK"
}

SKEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS penulis (
    id INTEGER PRIMARY KEY AUTOINCREMENT, nama_penulis TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS penerbit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, nama_penerbit TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS buku (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kode_buku TEXT NOT NULL UNIQUE, isbn TEXT NOT NULL, judul TEXT NOT NULL,
    kategori TEXT NOT NULL, id_penulis INTEGER NOT NULL, id_penerbit INTEGER NOT NULL,
    tahun_terbit INTEGER NOT NULL, stok_total INTEGER NOT NULL DEFAULT 0,
    stok_tersedia INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (id_penulis) REFERENCES penulis(id),
    FOREIGN KEY (id_penerbit) REFERENCES penerbit(id));
CREATE TABLE IF NOT EXISTS peminjam (
    id INTEGER PRIMARY KEY AUTOINCREMENT, nim TEXT NOT NULL UNIQUE,
    nama TEXT NOT NULL, program_studi TEXT NOT NULL, 
    jurusan TEXT NOT NULL, kelas TEXT NOT NULL, no_whatsapp TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS transaksi (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kode_transaksi TEXT NOT NULL UNIQUE,
    id_peminjam INTEGER NOT NULL, id_buku INTEGER NOT NULL,
    tanggal_pinjam TEXT NOT NULL, tanggal_tenggat TEXT NOT NULL,
    jumlah_perpanjang INTEGER NOT NULL DEFAULT 0, tanggal_kembali TEXT,
    hari_terlambat INTEGER NOT NULL DEFAULT 0, total_denda INTEGER NOT NULL DEFAULT 0,
    denda_lunas INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'DIPINJAM',
    FOREIGN KEY (id_peminjam) REFERENCES peminjam(id),
    FOREIGN KEY (id_buku) REFERENCES buku(id));
CREATE TABLE IF NOT EXISTS log_whatsapp (
    id INTEGER PRIMARY KEY AUTOINCREMENT, id_transaksi INTEGER NOT NULL,
    jenis TEXT NOT NULL, no_tujuan TEXT NOT NULL, isi_pesan TEXT NOT NULL,
    status TEXT NOT NULL, waktu_kirim TEXT NOT NULL,
    FOREIGN KEY (id_transaksi) REFERENCES transaksi(id));
CREATE TABLE IF NOT EXISTS pengaturan (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    lama_pinjam INTEGER NOT NULL DEFAULT 7,
    maks_perpanjang INTEGER NOT NULL DEFAULT 1,
    tarif_denda_per_hari INTEGER NOT NULL DEFAULT 1000,
    token_whatsapp TEXT DEFAULT '');
INSERT OR IGNORE INTO pengaturan (id) VALUES (1);
CREATE TABLE IF NOT EXISTS master_data_sync (
    sumber TEXT PRIMARY KEY, checksum TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS master_buku_ref (
    kode_sumber TEXT PRIMARY KEY,
    id_buku INTEGER NOT NULL UNIQUE,
    FOREIGN KEY (id_buku) REFERENCES buku(id) ON DELETE CASCADE);
"""

def koneksi():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def inisialisasi():
    conn = koneksi()
    try:
        conn.executescript(SKEMA)
        _migrasi_indeks_isbn(conn)
        _sinkronisasi_master_excel(conn)
        _sinkronkan_kode_kategori_buku(conn)
        conn.execute(
            "UPDATE transaksi SET denda_lunas=1 "
            "WHERE status='DIKEMBALIKAN'"
        )
        conn.commit()
    finally:
        conn.close()


def _migrasi_indeks_isbn(conn):
    unique_isbn = False
    for index in conn.execute("PRAGMA index_list(buku)").fetchall():
        if not index["unique"]:
            continue
        columns = [
            row["name"]
            for row in conn.execute(f"PRAGMA index_info('{index['name']}')").fetchall()
        ]
        if columns == ["isbn"]:
            unique_isbn = True
            break
    if not unique_isbn:
        return

    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(
            """CREATE TABLE buku_master_new (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               kode_buku TEXT NOT NULL UNIQUE, isbn TEXT NOT NULL, judul TEXT NOT NULL,
               kategori TEXT NOT NULL, id_penulis INTEGER NOT NULL,
               id_penerbit INTEGER NOT NULL, tahun_terbit INTEGER NOT NULL,
               stok_total INTEGER NOT NULL DEFAULT 0,
               stok_tersedia INTEGER NOT NULL DEFAULT 0,
               FOREIGN KEY (id_penulis) REFERENCES penulis(id),
               FOREIGN KEY (id_penerbit) REFERENCES penerbit(id))"""
        )
        conn.execute(
            """INSERT INTO buku_master_new
               (id,kode_buku,isbn,judul,kategori,id_penulis,id_penerbit,
                tahun_terbit,stok_total,stok_tersedia)
               SELECT id,kode_buku,isbn,judul,kategori,id_penulis,id_penerbit,
                      tahun_terbit,stok_total,stok_tersedia FROM buku"""
        )
        conn.execute("DROP TABLE buku")
        conn.execute("ALTER TABLE buku_master_new RENAME TO buku")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _file_excel(nama_dasar):
    root = Path(__file__).resolve().parent
    files = [
        path
        for folder in (root / "data", root)
        if folder.exists()
        for path in folder.glob("*.xlsx")
        if path.stem.casefold().startswith(nama_dasar.casefold())
    ]
    if not files:
        raise FileNotFoundError(
            f"File Excel '{nama_dasar}.xlsx' tidak ditemukan di folder project atau data."
        )
    return min(
        files,
        key=lambda path: (
            0 if path.stem.casefold() == nama_dasar.casefold() else 1,
            path.name.casefold(),
        ),
    )


def _baca_excel(path, sheet_name=0, header=0, wajib=()):
    frame = pd.read_excel(
        path, sheet_name=sheet_name, header=header, dtype=str, keep_default_na=False,
    )
    frame.columns = [str(column).strip() for column in frame.columns]
    missing = sorted(set(wajib) - set(frame.columns))
    if missing:
        raise ValueError(
            f"Kolom wajib tidak ditemukan pada '{path.name}': {', '.join(missing)}."
        )
    return frame


def _teks_excel(value):
    return str(value).strip()


def _angka_excel(value, kolom, baris):
    try:
        return int(float(_teks_excel(value)))
    except (TypeError, ValueError):
        raise ValueError(
            f"Nilai '{kolom}' pada baris {baris} harus berupa angka."
        ) from None


def _sinkronisasi_master_excel(conn):
    file_buku = _file_excel("Daftar 50 Buku")
    file_peminjam = _file_excel("Daftar nama kelas")
    buku = _baca_excel(
        file_buku,
        header=1,
        wajib=("ID Buku", "Kategori", "Judul", "Pengarang", "Penerbit", "ISBN",
               "Total Buku", "Tersedia"),
    )
    peminjam = _baca_excel(
        file_peminjam,
        sheet_name="Data Peminjam",
        wajib=("NIM", "Nama", "Kelas", "Program Studi", "Jurusan"),
    )

    checksum_buku = hashlib.sha256(file_buku.read_bytes()).hexdigest()
    checksum_peminjam = hashlib.sha256(file_peminjam.read_bytes()).hexdigest()
    last_buku = conn.execute(
        "SELECT checksum FROM master_data_sync WHERE sumber='buku'"
    ).fetchone()
    last_peminjam = conn.execute(
        "SELECT checksum FROM master_data_sync WHERE sumber='peminjam'"
    ).fetchone()
    buku_berubah = not last_buku or last_buku["checksum"] != checksum_buku
    if not buku_berubah:
        buku_berubah = any(
            not conn.execute(
                "SELECT 1 FROM master_buku_ref WHERE kode_sumber=?",
                (_teks_excel(value),),
            ).fetchone()
            for value in buku["ID Buku"]
            if _teks_excel(value)
        )
    peminjam_berubah = (
        not last_peminjam or last_peminjam["checksum"] != checksum_peminjam
    )
    for row in peminjam.to_dict("records"):
        nim = _teks_excel(row["NIM"])
        existing = conn.execute(
            """SELECT nama, program_studi, jurusan, kelas
               FROM peminjam WHERE nim=?""",
            (nim,),
        ).fetchone()
        if not existing or (
            existing["nama"] != _teks_excel(row["Nama"])
            or existing["program_studi"] != _teks_excel(row["Program Studi"])
            or existing["jurusan"] != _teks_excel(row["Jurusan"])
            or existing["kelas"] != _teks_excel(row["Kelas"])
        ):
            peminjam_berubah = True
            break

    global DATA_JURUSAN_PRODI_KELAS
    data_jurusan = {}
    for row in peminjam.to_dict("records"):
        jurusan = _teks_excel(row["Jurusan"])
        prodi = _teks_excel(row["Program Studi"])
        kelas = _teks_excel(row["Kelas"])
        if jurusan and prodi and kelas:
            data_jurusan.setdefault(jurusan, set()).add((prodi, kelas))
    DATA_JURUSAN_PRODI_KELAS = {
        jurusan: sorted(daftar)
        for jurusan, daftar in sorted(data_jurusan.items())
    }

    if not buku_berubah and not peminjam_berubah:
        return

    conn.execute("BEGIN")
    try:
        if buku_berubah:
            for baris, row in enumerate(buku.to_dict("records"), start=3):
                kode_sumber = _teks_excel(row["ID Buku"])
                judul = _teks_excel(row["Judul"])
                kategori = _teks_excel(row["Kategori"])
                penulis = _teks_excel(row["Pengarang"])
                penerbit = _teks_excel(row["Penerbit"])
                isbn = _teks_excel(row["ISBN"])
                if not any((kode_sumber, judul, kategori, penulis, penerbit, isbn)):
                    continue
                if not all((kode_sumber, judul, kategori, penulis, penerbit, isbn)):
                    raise ValueError(
                        f"Data buku pada baris {baris} belum lengkap."
                    )
                if kategori not in KATEGORI_BUKU:
                    raise ValueError(
                        f"Kategori '{kategori}' pada baris {baris} belum terdaftar."
                    )
                total = _angka_excel(row["Total Buku"], "Total Buku", baris)
                tersedia = _angka_excel(row["Tersedia"], "Tersedia", baris)
                if total < 0 or tersedia < 0 or tersedia > total:
                    raise ValueError(
                        f"Stok buku pada baris {baris} tidak valid."
                    )

                conn.execute(
                    "INSERT OR IGNORE INTO penulis (nama_penulis) VALUES (?)",
                    (penulis,),
                )
                id_penulis = conn.execute(
                    "SELECT id FROM penulis WHERE nama_penulis=?", (penulis,)
                ).fetchone()["id"]
                conn.execute(
                    "INSERT OR IGNORE INTO penerbit (nama_penerbit) VALUES (?)",
                    (penerbit,),
                )
                id_penerbit = conn.execute(
                    "SELECT id FROM penerbit WHERE nama_penerbit=?", (penerbit,)
                ).fetchone()["id"]
                existing = conn.execute(
                    """SELECT b.id, b.tahun_terbit FROM master_buku_ref r
                       JOIN buku b ON b.id=r.id_buku WHERE r.kode_sumber=?""",
                    (kode_sumber,),
                ).fetchone()
                if not existing:
                    existing = conn.execute(
                        "SELECT id, tahun_terbit FROM buku WHERE kode_buku=?",
                        (kode_sumber,),
                    ).fetchone()
                if not existing:
                    existing = conn.execute(
                        """SELECT b.id, b.tahun_terbit FROM buku b
                           JOIN penulis pn ON pn.id=b.id_penulis
                           JOIN penerbit pb ON pb.id=b.id_penerbit
                           WHERE b.isbn=? AND b.judul=? AND b.kategori=?
                             AND pn.nama_penulis=? AND pb.nama_penerbit=?
                             AND b.id NOT IN (
                                 SELECT id_buku FROM master_buku_ref
                                 WHERE kode_sumber != ?
                             )
                           ORDER BY b.id LIMIT 1""",
                        (isbn, judul, kategori, penulis, penerbit, kode_sumber),
                    ).fetchone()
                id_buku = existing["id"] if existing else None
                dipinjam = 0
                if id_buku is not None:
                    dipinjam = conn.execute(
                        """SELECT COUNT(*) FROM transaksi
                           WHERE id_buku=? AND status IN ('DIPINJAM','TERLAMBAT')""",
                        (id_buku,),
                    ).fetchone()[0]
                stok_tersedia = max(min(tersedia, total) - dipinjam, 0)
                tahun_terbit = existing["tahun_terbit"] if existing else 0

                if existing:
                    conn.execute(
                        """UPDATE buku SET isbn=?, judul=?, kategori=?, id_penulis=?,
                           id_penerbit=?, tahun_terbit=?, stok_total=?, stok_tersedia=?
                           WHERE id=?""",
                        (isbn, judul, kategori, id_penulis, id_penerbit, tahun_terbit,
                         total, stok_tersedia, id_buku),
                    )
                else:
                    conn.execute(
                        """INSERT INTO buku
                           (kode_buku,isbn,judul,kategori,id_penulis,id_penerbit,
                            tahun_terbit,stok_total,stok_tersedia)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (f"__MASTER_{kode_sumber}__", isbn, judul, kategori, id_penulis, id_penerbit,
                         tahun_terbit, total, stok_tersedia),
                    )
                    id_buku = conn.execute(
                        "SELECT id FROM buku WHERE kode_buku=?",
                        (f"__MASTER_{kode_sumber}__",),
                    ).fetchone()["id"]
                conn.execute(
                    """INSERT INTO master_buku_ref (kode_sumber,id_buku) VALUES (?,?)
                       ON CONFLICT(kode_sumber) DO UPDATE SET id_buku=excluded.id_buku""",
                    (kode_sumber, id_buku),
                )
            conn.execute(
                """INSERT INTO master_data_sync (sumber,checksum) VALUES ('buku',?)
                   ON CONFLICT(sumber) DO UPDATE SET checksum=excluded.checksum""",
                (checksum_buku,),
            )

        if peminjam_berubah:
            for baris, row in enumerate(peminjam.to_dict("records"), start=2):
                nim = _teks_excel(row["NIM"])
                nama = _teks_excel(row["Nama"])
                kelas = _teks_excel(row["Kelas"])
                prodi = _teks_excel(row["Program Studi"])
                jurusan = _teks_excel(row["Jurusan"])
                if not any((nim, nama, kelas, prodi, jurusan)):
                    continue
                if not all((nim, nama, kelas, prodi, jurusan)):
                    raise ValueError(
                        f"Data peminjam pada baris {baris} belum lengkap."
                    )
                conn.execute(
                    """INSERT INTO peminjam
                       (nim,nama,program_studi,jurusan,kelas,no_whatsapp)
                       VALUES (?,?,?,?,?,'')
                       ON CONFLICT(nim) DO UPDATE SET
                       nama=excluded.nama, program_studi=excluded.program_studi,
                       jurusan=excluded.jurusan, kelas=excluded.kelas""",
                    (nim, nama, prodi, jurusan, kelas),
                )
            conn.execute(
                """INSERT INTO master_data_sync (sumber,checksum) VALUES ('peminjam',?)
                   ON CONFLICT(sumber) DO UPDATE SET checksum=excluded.checksum""",
                (checksum_peminjam,),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _sinkronkan_kode_kategori_buku(conn):
    rows = conn.execute(
        "SELECT id, kode_buku, kategori FROM buku ORDER BY id"
    ).fetchall()
    kode_terpakai = {}
    perlu_diubah = []
    for row in rows:
        prefix = KATEGORI_BUKU.get(row["kategori"], "UM")
        match = re.fullmatch(rf"{re.escape(prefix)}-(\d{{3}})", row["kode_buku"])
        if match:
            kode_terpakai.setdefault(prefix, set()).add(int(match.group(1)))
        else:
            perlu_diubah.append((row["id"], prefix))

    if not perlu_diubah:
        return

    conn.execute("BEGIN")
    try:
        for id_buku, _ in perlu_diubah:
            conn.execute(
                "UPDATE buku SET kode_buku=? WHERE id=?",
                (f"__MIGRASI_KODE_{id_buku}__", id_buku),
            )

        nomor_berikutnya = {}
        for id_buku, prefix in perlu_diubah:
            nomor = nomor_berikutnya.get(prefix, 1)
            while nomor in kode_terpakai.setdefault(prefix, set()):
                nomor += 1
            conn.execute(
                "UPDATE buku SET kode_buku=? WHERE id=?",
                (f"{prefix}-{nomor:03d}", id_buku),
            )
            kode_terpakai[prefix].add(nomor)
            nomor_berikutnya[prefix] = nomor + 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def q(sql, params=()):
    conn = koneksi()
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows

def x(sql, params=()):
    conn = koneksi()
    cur = conn.execute(sql, params)
    conn.commit()
    last = cur.lastrowid
    conn.close()
    return last

def pengaturan():
    return q("SELECT * FROM pengaturan WHERE id=1")[0]

def proses_peminjaman(id_peminjam, id_buku, no_whatsapp, hari_ini):
    conn = koneksi()
    try:
        conn.execute("BEGIN IMMEDIATE")
        aktif = conn.execute(
            "SELECT kode_transaksi FROM transaksi WHERE id_peminjam=? AND status IN ('DIPINJAM','TERLAMBAT')",
            (id_peminjam,),
        ).fetchone()
        if aktif:
            raise ValueError(
                f"Anggota masih memiliki pinjaman aktif ({aktif['kode_transaksi']})."
            )
        anggota = conn.execute(
            "SELECT id FROM peminjam WHERE id=?", (id_peminjam,)
        ).fetchone()
        buku = conn.execute(
            "SELECT stok_tersedia FROM buku WHERE id=?", (id_buku,)
        ).fetchone()
        if not anggota:
            raise ValueError("Data anggota tidak ditemukan.")
        if not buku or buku["stok_tersedia"] < 1:
            raise ValueError("Buku yang dipilih sudah tidak tersedia.")

        setting = conn.execute(
            "SELECT lama_pinjam FROM pengaturan WHERE id=1"
        ).fetchone()
        tenggat = hari_ini + timedelta(days=setting["lama_pinjam"])
        prefix = f"TRX-{hari_ini.strftime('%Y%m%d')}-"
        row = conn.execute(
            "SELECT kode_transaksi FROM transaksi WHERE kode_transaksi LIKE ? ORDER BY id DESC LIMIT 1",
            (prefix + "%",),
        ).fetchone()
        urut = int(row["kode_transaksi"].split("-")[-1]) + 1 if row else 1
        kode = f"{prefix}{urut:03d}"
        cur = conn.execute(
            """INSERT INTO transaksi (kode_transaksi,id_peminjam,id_buku,
               tanggal_pinjam,tanggal_tenggat,status) VALUES (?,?,?,?,?,?)""",
            (kode, id_peminjam, id_buku, str(hari_ini), str(tenggat), "DIPINJAM"),
        )
        cur_id = cur.lastrowid
        stok = conn.execute(
            "UPDATE buku SET stok_tersedia=stok_tersedia-1 WHERE id=? AND stok_tersedia>0",
            (id_buku,),
        )
        if stok.rowcount != 1:
            raise ValueError("Buku yang dipilih sudah tidak tersedia.")
        conn.execute(
            "UPDATE peminjam SET no_whatsapp=? WHERE id=?",
            (no_whatsapp, id_peminjam),
        )
        conn.commit()
        return cur_id, kode, tenggat
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def proses_pengembalian(id_transaksi, hari_ini):
    conn = koneksi()
    try:
        conn.execute("BEGIN IMMEDIATE")
        transaksi = conn.execute(
            """SELECT t.*, b.id AS buku_id FROM transaksi t
               JOIN buku b ON b.id=t.id_buku
               WHERE t.id=? AND t.status IN ('DIPINJAM','TERLAMBAT')""",
            (id_transaksi,),
        ).fetchone()
        if not transaksi:
            raise ValueError("Transaksi tidak ditemukan atau sudah selesai.")

        setting = conn.execute(
            "SELECT tarif_denda_per_hari FROM pengaturan WHERE id=1"
        ).fetchone()
        tenggat = datetime.strptime(
            transaksi["tanggal_tenggat"], "%Y-%m-%d"
        ).date()
        hari_terlambat = max((hari_ini - tenggat).days, 0)
        total_denda = hari_terlambat * setting["tarif_denda_per_hari"]
        conn.execute(
            """UPDATE transaksi SET tanggal_kembali=?, hari_terlambat=?,
               total_denda=?, denda_lunas=1, status='DIKEMBALIKAN' WHERE id=?""",
            (str(hari_ini), hari_terlambat, total_denda, id_transaksi),
        )
        conn.execute(
            "UPDATE buku SET stok_tersedia=stok_tersedia+1 WHERE id=?",
            (transaksi["buku_id"],),
        )
        conn.commit()
        return hari_terlambat, total_denda
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def validasi_nim(nim):
    return isinstance(nim, str) and re.fullmatch(r"[0-9]+", nim) is not None

def validasi_wa(nomor):
    return isinstance(nomor, str) and re.fullmatch(r"62[0-9]{8,13}", nomor) is not None

def refresh_status():
    tarif = pengaturan()["tarif_denda_per_hari"]
    today = date.today()
    for t in q("SELECT * FROM transaksi WHERE status IN ('DIPINJAM','TERLAMBAT')"):
        tenggat = datetime.strptime(t["tanggal_tenggat"], "%Y-%m-%d").date()
        if today > tenggat:
            telat = (today - tenggat).days
            x("UPDATE transaksi SET status='TERLAMBAT',hari_terlambat=?,total_denda=? WHERE id=?",
              (telat, telat * tarif, t["id"]))
        else:
            x("UPDATE transaksi SET status='DIPINJAM',hari_terlambat=0,total_denda=0 WHERE id=?",
              (t["id"],))

def hitung_denda(t):
    if t["status"] == "DIKEMBALIKAN":
        return t["hari_terlambat"], t["total_denda"]
    tenggat = datetime.strptime(t["tanggal_tenggat"], "%Y-%m-%d").date()
    today = date.today()
    if today > tenggat:
        telat = (today - tenggat).days
        return telat, telat * pengaturan()["tarif_denda_per_hari"]
    return 0, 0

def cek_boleh_pinjam(id_peminjam):
    aktif = q("SELECT * FROM transaksi WHERE id_peminjam=? AND status IN ('DIPINJAM','TERLAMBAT')", (id_peminjam,))
    if aktif:
        t = aktif[0]
        return False, f"Gagal: Mahasiswa masih memiliki pinjaman aktif ({t['kode_transaksi']}). Harap kembalikan buku terlebih dahulu."
    
    return True, ""

def kode_transaksi_baru():
    prefix = f"TRX-{date.today().strftime('%Y%m%d')}-"
    rows = q("SELECT kode_transaksi FROM transaksi WHERE kode_transaksi LIKE ? ORDER BY id DESC LIMIT 1", (prefix + "%",))
    urut = int(rows[0]["kode_transaksi"].split("-")[-1]) + 1 if rows else 1
    return f"{prefix}{urut:03d}"

def generate_kode_buku(kategori):
    kode_kat = KATEGORI_BUKU.get(kategori, "UM")
    rows = q(
        "SELECT kode_buku FROM buku WHERE kode_buku LIKE ?",
        (f"{kode_kat}-%",),
    )
    nomor_terpakai = []
    for row in rows:
        prefix, separator, nomor = row["kode_buku"].rpartition("-")
        if prefix == kode_kat and separator and nomor.isdigit():
            nomor_terpakai.append(int(nomor))
    urut = max(nomor_terpakai, default=0) + 1
    return f"{kode_kat}-{urut:03d}"