# Yazı tipi kaynağı

IBM Plex Mono ve IBM Plex Sans, **SIL Open Font License 1.1** ile lisanslıdır (`OFL-IBM-Plex.txt`,
Copyright © 2017 IBM Corp., ayrılmış font adı "Plex"). Lisans metni IBM'in resmi deposundan alındı:
<https://github.com/IBM/plex> (`LICENSE.txt`).

Dosyalar IBM'in npm paketlerinden (`@ibm/plex-mono@1.1.0`, `@ibm/plex-sans@1.1.0`, `fonts/complete/woff2/`)
**değiştirilmeden** alındı; yeniden adlandırma, alt kümeleme ya da dönüştürme yapılmadı. Çalışma anında harici
istek yoktur; dosyalar `style.css` içindeki `@font-face` ile yerelden yüklenir.

| Dosya | SHA-256 |
|---|---|
| `IBMPlexMono-Regular.woff2` | `49ce58b41a0e1cb921c0f58d9a5b8b96a2cc21437c7066f3ba4f24873076d131` |
| `IBMPlexMono-Medium.woff2` | `8c2c290cbd998fa1f647e4572aca6ebbd72589551b0f3f9f8bb8628fbb8219d5` |
| `IBMPlexMono-SemiBold.woff2` | `ed5eaca7522336959d6c3810bd9bb78424f0d964082d581bfbea169ee08d14e3` |
| `IBMPlexSans-Regular.woff2` | `ba711a3085ff9f27440b6b9c4550cfc47c97bf36591d5da958b975bb3add8c1a` |
| `IBMPlexSans-SemiBold.woff2` | `f78048030eab62e860efa39a0df79e2e5581bf122eb95b9bc42c0b8a4988d205` |

Font yüklenemezse `--font-mono` ve `--font-sans` sistem yazı tiplerine düşer (`css/tokens.css`).
