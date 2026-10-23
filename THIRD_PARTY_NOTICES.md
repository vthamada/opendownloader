# Componentes distribuídos

O aplicativo contém código autoral deste projeto e componentes de terceiros.
Os textos de licença disponíveis nos runtimes/pacotes usados no build ficam em
`notices/` e nos metadados de `cryptography` em `_internal/`.

- CPython 3.12.10 e sua biblioteca padrão, incluindo SSL e SQLite.
- Tcl/Tk 8.6, para a interface desktop.
- cryptography 50.0.2 e cffi 2.1.1, para autenticação de releases Ed25519.
- PyInstaller 6.22.3 e bootloader, para o pacote Windows.
- yt-dlp 2026.8.19 (Unlicense) e yt-dlp-ejs 0.8.0, para resolução de mídia pública.
- Brotli, certifi, requests, urllib3, charset-normalizer, idna, mutagen,
  PyCryptodomex e websockets: dependências da distribuição padrão do yt-dlp.
- Dependências de build identificadas integralmente em `requirements-lock.txt`.

No instalador completo 1.7.1, acompanham componentes privados:

- Node.js 24.22.1, com licença e avisos de suas dependências em `runtimes/node/legal`.
  Não inclui npm e não altera instalação global ou PATH.
- FFmpeg/ffprobe da revisão `c89d6ee1ca848314270155ceb13b1cab3744fc3c`, compilados
  para o Lume sem bibliotecas externas, GPL/nonfree/version3 ou rede dentro do
  FFmpeg. Configuração, licenças e proveniência ficam em `runtimes/ffmpeg/legal`
  e `runtime-provenance.json`. A fonte correspondente acompanha o próprio
  instalador em `runtimes/ffmpeg/legal`, com receita e identidade do compilador
  portátil; não é um componente que o usuário precise instalar separadamente.
- Partes de FFmpeg são baseadas no trabalho do Independent JPEG Group.
- Os avisos do runtime GCC/MinGW e a GCC Runtime Library Exception acompanham a
  revisão do compilador usado. O compilador não é instalado nem distribuído
  como parte do aplicativo.

Fonte FFmpeg: https://github.com/FFmpeg/FFmpeg/tree/c89d6ee1ca848314270155ceb13b1cab3744fc3c
e Node: https://nodejs.org/dist/v24.22.1/.
A build BtbN usada na homologação local 1.7.0 foi substituída após a auditoria
confirmar ligação com FFTW GPL, apesar do nome LGPL do asset. O pacote 1.7.0
permanece histórico e não deve ser usado como entrega a terceiros.

Não são distribuídos componentes, código ou recursos do IDM.
O executável local gerado não tem assinatura comercial de código; nenhuma chave
de assinatura de releases de produção é criada ou incluída no pacote.
