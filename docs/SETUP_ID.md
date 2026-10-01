# Panduan setup (Bahasa Indonesia)

Semua langkah bisa dilakukan **tanpa kartu kredit** memakai BigQuery sandbox.

## 1. Project GCP
1. Buka https://console.cloud.google.com/bigquery dan login dengan akun Google pribadi (bukan akun kantor).
2. Buat project baru, mis. `elson-bqgov`. Sandbox aktif otomatis selama billing tidak dipasang.
3. Pastikan **BigQuery API** aktif (biasanya sudah).

Batas sandbox yang relevan: query 1 TiB/bulan, storage 10 GiB seumur hidup (tidak kembali walau tabel dihapus),
tanpa DML (`INSERT/UPDATE/DELETE`), tabel kedaluwarsa setelah 60 hari. Toolkit dan demo sudah disesuaikan dengan batas ini.

## 2. Instal dan login (WSL)
```bash
cd ~/projects && git clone git@github.com:elsonsaputra03-dot/bq-governance-toolkit.git && cd bq-governance-toolkit
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,ai]"
# gcloud CLI: https://cloud.google.com/sdk/docs/install
gcloud auth application-default login
gcloud config set project elson-bqgov
pytest -q                               # semua test harus lulus (offline)
```

## 3. Warehouse demo
```bash
bqgov demo-sql > /tmp/setup.sql
bq query --use_legacy_sql=false < /tmp/setup.sql      # atau tempel isinya di BigQuery console lalu Run
```
Hasil: dataset `gov_raw`, `gov_staging`, `gov_mart` (< 100 MB) dari `thelook_ecommerce`, tanpa kolom nama/email/alamat.

## 4. Riwayat job
```bash
python demo/workload.py --project elson-bqgov --rounds 2
```
Jalankan di **2–3 hari berbeda** supaya tren biaya per hari terisi. Satu putaran ±1 GB dari kuota 1 TiB.

## 5. Izin IAM (wajib, walaupun Anda Owner)
Role Owner **tidak** mencakup akses ke `INFORMATION_SCHEMA.TABLE_STORAGE`. Tambahkan secara eksplisit (berlaku 1–2 menit):
```bash
P=elson-bqgov; U=$(gcloud config get-value account)
for R in roles/bigquery.metadataViewer roles/bigquery.resourceViewer; do
  gcloud projects add-iam-policy-binding $P --member="user:$U" --role="$R" --condition=None --quiet
done
```

Lalu aktifkan pengumpulan data storage (sekali per project; data muncul dalam ±1 hari, sementara itu toolkit memakai Tables API):
```bash
bq query --use_legacy_sql=false 'ALTER PROJECT `elson-bqgov` SET OPTIONS (`region-us.enable_info_schema_storage` = TRUE)'
```

## 6. Jalankan toolkit
```bash
cp config.example.yaml config.yaml      # isi project: elson-bqgov
bqgov -v run
cat snapshots/REPORT.md
```
Exit code 1 berarti ada cek DQ yang `fail` (berguna di CI). Contoh: `customer_ltv` di demo memang tidak dibangun ulang tiap hari.

## 7. Deskripsi dengan AI (opsional)
```bash
export GEMINI_API_KEY=...               # dari Google AI Studio; atau set describe.provider: ollama
bqgov describe                          # menulis descriptions.yaml, tidak mengubah BigQuery
# tinjau & edit file, ubah reviewed: true pada tabel yang sudah benar
bqgov describe --apply descriptions.yaml
```
