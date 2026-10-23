# OpenDownloader 1.10.0 — chaves autorizadas

Esta versão adiciona processamento local de HLS SAMPLE-AES/identity em MPEG-TS
H.264/AAC e DASH com proteção MP4 CENC (`cenc`, `cens`, `cbc1` e `cbcs`).
O componente OpenDownloaderCrypto usa as bibliotecas privadas
do FFmpeg já incluídas na instalação. Não solicita licenças DRM, não lê cookies,
não extrai chaves do navegador e não altera o CDM.

## Uso

Envie o manifesto HLS/DASH pela extensão do Chrome/Edge ou por Novo download.
HLS identity lê a chave HTTP(S) declarada pela origem, respeitando TLS e a
recusa de downgrade. Para CENC, informe pares `KID:CHAVE`, cada elemento com
32 dígitos hexadecimais, no campo mascarado da confirmação. Separe até 16 pares
por ponto e vírgula. A extensão nunca transporta essas chaves.

As chaves permanecem na memória da sessão. A fila salva somente que será
necessário informá-las novamente após fechar o aplicativo. Ao continuar ou
reiniciar a tarefa, a interface solicita a reentrada quando necessária.
No terminal, `--keys-stdin` aceita os pares somente pela entrada padrão junto
de `--download`; nunca coloque chaves na linha de comando ou na URL.

## Processamento e verificação

Os segmentos continuam sendo transferidos em paralelo, com os limites existentes.
O cache guarda os bytes criptografados e comprovantes de identidade. Descritores
locais gerados associam cada segmento à sua chave e IV, preservando essa associação
até os últimos pacotes do segmento. O componente recebe chaves por um pipe de entrada padrão e recusa
leituras externas. As bibliotecas privadas não têm rede habilitada.

O processamento verifica a decodificação completa e copia os pacotes para MP4
sem recodificar. Essa verificação acrescenta custo de CPU e disco. Uma falha
impede publicar o arquivo final. AES-CBC/CTR não autentica o conteúdo: decodificar
sem erros não substitui um SHA-256 esperado obtido de fonte confiável.

Chaves não são gravadas em arquivos, fila, argumentos, ambiente ou logs pelo
aplicativo. Não se promete apagamento seguro da memória, de swap ou de dumps
produzidos pelo sistema operacional. A posse dos temporários continua retida
enquanto a limpeza de um processo filho estiver pendente.

## Escopo

Somente VOD completo, limitado a sete dias, 10.000 segmentos, 64 GiB e duas faixas.
Cada processamento admite até 16 chaves. Mantêm-se os limites existentes de
containers, codecs disponíveis e recursos não suportados de HLS/DASH.
SAMPLE-AES em fMP4, outros codecs de SAMPLE-AES, HLS SAMPLE-AES-CTR e integração
com sistemas de licenciamento DRM continuam fora do escopo validado.
KIDs diferentes podem identificar faixas diferentes; rotação CENC de KID dentro
da mesma faixa não é anunciada como suportada. Mudança das chaves explícitas
invalida a identidade do cache e exige Reiniciar.

O relatório do pacote registra os modos efetivamente exercitados. A existência
de suporte a um algoritmo na biblioteca não comprova todos os empacotamentos,
codecs, DRM ou sites. A validação das extensões reais deve registrar separadamente
Chrome e Edge; testes de CLI e simulações de navegador não substituem essa prova.

## Referências técnicas

- [Apple — MPEG-2 Stream Encryption Format for HLS](https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/HLS_Sample_Encryption/Encryption/Encryption.html).
- [RFC 8216 — HLS](https://www.rfc-editor.org/rfc/rfc8216).
- FFmpeg: fonte original fixada no commit `c89d6ee1ca848314270155ceb13b1cab3744fc3c`,
  incluída com os avisos e fontes do componente privado. O helper próprio é
  distribuído com seu fonte e receita de compilação, sem modificar o FFmpeg.
