# BAG v0.5.0 — Arsitektur Browser Engine

## Modul

- `gateway/browser_manager.py`: lifecycle Chromium, profil persisten, CDP (loopback), tab dengan ID opaque, restart.
- `gateway/core.py`: facade kompatibel untuk pemetaan DOM, aksi, perekaman respons, pengecekan navigasi, audit.
- `gateway/site_profiles.py`: validasi profil selector per origin yang telah diizinkan oleh allowlist.
- `gateway/migrations.py`: migrasi SQLite additive ke `PRAGMA user_version=2`, dengan backup online sebelum migrasi.
- `gateway/jobs.py`: antrean async terbatas; satu worker; tanpa retry otomatis.
- `gateway/api.py`: API localhost dengan Bearer, Host/Origin guard, route versi lama dan baru.

## Keamanan dan status

`config.json` / `allowed_origins` tetap sumber izin navigasi. Site profile tidak bisa memperluas origin. Browser profile bukan perluasan izin: pergantian profil me-restart browser, tetap menggunakan allowlist yang sama. Tab popup tidak otomatis dijadikan tab aktif. Perintah restart atau berganti profil ditolak jika ada job queued/running. Watcher berhenti saat browser di-restart.

Jobs (`navigate`, `chat`, `fill`, `click`, `read`, `probe`) mencatat action, status dan kode error di SQLite, tetapi tidak menyimpan prompt/konten halaman. Hasil job hanya di RAM dan hilang setelah restart. Job yang sedang berjalan saat proses mati diberi status `interrupted` pada start berikutnya, bukan dikirim ulang: prompt yang dikirim mungkin sudah diterima website.

Saat pertama kali membuka database v0.4.1, dibuat snapshot SQLite di `data/backups/pre-schema-v2-*.sqlite3` sebelum migrasi. Upgrade source tidak membaca atau menyalin data browser, cookie, database, maupun `config.json`. Nama profil valid menggunakan regex huruf kecil/angka/underscore/dash, maksimal 32 karakter, dan jalurnya hanya di dalam `data/`.

## Batasan milestone

Belum ada retry browser crash otomatis sepenuhnya, idempotency antar klien, workflow DSL, pemetaan viewport versi 2, ataupun worker paralel multi-profil. Endpoint sinkron lama tetap tersedia agar kompatibel; koordinasi beberapa klien yang menulis ke satu browser masih tanggung jawab operator sampai dispatcher terpadu v0.7. Tidak ada bypass CAPTCHA/Cloudflare.

## v0.6.0 additions

`gateway/precision.py` contains pure presentation/snapshot/anchor guards. `Engine.capture()` owns latest ephemeral `snapshot_id` in process memory. `map_evidence` stores active map's presentation metadata plus semantic anchor. `map_revisions` stores ordered map revisions. Updating a map retains stable `maps.id`. The API is loopback and bearer authenticated; `preview` and `revisions` are read-only. The mapper dashboard always includes a recent snapshot ID; legacy API callers may omit it but cannot claim screenshot-bound mapping. No third-party CAPTCHA/Cloudflare bypass.

The `maps`/`changes` v0.4 schema and `site_profiles`/`jobs` v0.5 schema are preserved during migration to schema v3. Migration uses SQLite online backups. Evidence stores no observed chatbot text or input values. Remote CDP is bound to the same access and mapping rules; no unsupported RDP/VNC guarantee.
