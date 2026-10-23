# Validação

## OpenDownloader 1.12.1

Correção do tratamento de descrições MP4 protegidas no fluxo de mídia e na
recuperação de publicação. Nove regressões focadas passaram. O caso `v2.mp4`
foi identificado como protegido por inspeção local, sem alterar o original.
Estado final da geração, aplicação e limites:
`dist/OpenDownloaderSetup-1.12.1-VALIDACAO.md`.
Documentação: [OPENDOWNLOADER-1.12.1](docs/OPENDOWNLOADER-1.12.1.md).
Os resultados anteriores abaixo são históricos.

## OpenDownloader 1.12.0

Esta entrega local acrescenta escolha de áudio HLS, prévia silenciosa, bitrate
solicitado, limite de duração e diagnóstico EME. O procedimento e os limites
estão em [OPENDOWNLOADER-1.12.0](docs/OPENDOWNLOADER-1.12.0.md).

Os resultados finais desta geração ficam em
`dist/OpenDownloaderSetup-1.12.0-VALIDACAO.md`, junto ao instalador e seu SHA-256.
Esse relatório distingue testes de código, pacote congelado, instalação aplicada
e testes interativos reais em cada navegador. A existência de um EXE não comprova
captura/download no Chrome e Edge. Evidências locais: `.cache/release-1.12.0/`.

Integração oficial de DRM, login em serviços, licenças offline, extração de
chaves do CDM e exploração de licenciamento de terceiros não foram implementados.
Assinatura de produção, publicação nas lojas e VM limpa permanecem sem validação.
Os resultados anteriores abaixo são históricos e não validam a versão atual.

## OpenDownloader 1.11.0

Aplicado localmente com backup da 1.10.0 e leitura de volta dos 1.167 arquivos.
O pacote passou em 19 verificações reais, incluindo recebimento nativo e salvamento
de MP4/WebM, com PATH vazio e ferramentas privadas. A extensão é 0.4.0; seu ID e
permissões permanecem iguais. Chrome e Edge têm registros válidos nas duas views.

A captura real foi concluída pelo usuário no Chrome e no Edge. Dois MP4 com
H.264/AAC foram decodificados integralmente e conferidos com movimento, cores e
tom de 440 Hz da fixture. Os controles/seletor foram operados pelo usuário;
a política da automação bloqueia a leitura de páginas da extensão. A suíte final
teve 492 aprovações e 4 casos ignorados; os 20 testes Node também passaram.
O estado atualizado e os limites ficam
em `dist/OpenDownloaderSetup-1.11.0-VALIDACAO.md` e em
[gravação da reprodução](docs/OPENDOWNLOADER-1.11.0.md). O `VALIDACAO.md` incluído
no pacote preserva o histórico anterior à conclusão desta instalação; use o
relatório externo da mesma versão para seu estado final.

## Histórico — Lume 1.7.1

**Estado naquela entrega:** Lume 1.7.1 instalado pelo assistente unificado, com componentes
privados, 340 testes de fonte (1 ignorado) e 14 verificações reais da instalação.
Instalação/reparo/desinstalação e abertura via host foram comprovados; os menus
reais dos navegadores, publicação nas lojas, assinatura de produção e VM limpa
continuam com limites explícitos. A build própria de FFmpeg inclui fonte e avisos
no próprio instalador. Veja [relatório 1.7.1](docs/INSTALADOR-1.7.1.md).

## Histórico — Lume Downloads 1.6.0

Windows x64, 21/10/2026. Aplicado com backup após **274 testes Python**
(202,389 s), cinco arquivos Node e **13 verificações reais do pacote**.
Fonte/versão do executável e hashes instalados coincidem com o staging validado.
Fila de produção preservada; testes e benchmark usaram perfis isolados.

Entregues: buffer streaming 256 KiB com reserva de disco proporcional e regressão
de cancelamento, quatro subfases de remux, filas nomeadas persistentes/suspensão,
pastas por categoria explícitas, atalhos locais/foco e extensão **0.3.2** com
diagnóstico da ponte. ID e permissões da extensão não mudaram. Os registros de
Chrome/Edge foram lidos nas duas views e apontam para o host aplicado.

O executável 1.6 baixou o mesmo episódio 2880×2160 em **57,5024 s até publicação**
e **59,7612 s com limpeza**, contra 60,4241/62,6859 s na repetição final da 1.5.
CPU da família observada 34,140625 s, pico RSS concorrente amostrado 110.911.488 B,
cinco processos observados, sem erros do coletor. A última repetição IDM desta
sessão foi 59,2879 s; não foi reexecutada na rodada 1.6. Cache, CDN e atividade
do usuário não são controlados; a diferença não comprova superioridade geral.
Saída 2.232.294.941 B, hash independente igual à fila e à saída 1.5:
`7f7fb08ef726718ed9af20d582460c6141a611d4eec2aa10297e4a567017dd3e`.
O diretório temporário foi removido.

A rodada real **Chrome/Edge 1.6 ainda aguarda envios**. O observador começou sem
processos Lume; até a última conferência só houve acesso à página, sem arquivos
enviados. Simulações e chamadas diretas do host não provam o fluxo pelo navegador.
Páginas usadas naquela rodada: `http://127.0.0.1:50677/chrome/` e `/edge/`.
A fixture foi encerrada depois da validação do instalador; esses endereços não
são um serviço permanente do produto.

Entrega escolhida pelo usuário: **ZIP local + SHA-256**, sem domínio/feed/chave
privada de produção. Instruções em `docs/DISTRIBUICAO-LOCAL.md`. HTTP/2 foi testado
em protótipo TLS, mas recusado para integração após esgotar streams com cancelamentos
repetidos no backend avaliado. HTTP/3, proxy, cookies/login, FTP, DRM, recorrência
e divisão de intervalos já em andamento não foram adicionados. Não há suporte universal.
Recibos: `.cache/validation-1.6-tests.txt`, `.cache/package-verification.json` e
`../.cache/lume-evolution-1.6/RESULTADO.md`. Sem commit/push/publicação externa.

## Histórico

# Validação — Lume Downloads 1.5.0

Windows x64, 21/10/2026. O executável final foi aplicado após os testes de pacote,
com backup da 1.4 e do candidato intermediário. A fila de produção foi preservada;
os benchmarks usaram perfis e destinos isolados. Registros Chrome/Edge permanecem
apontando para o host na pasta instalada. Extensão mantida em 0.3.1, sem novas permissões.

A suíte completa do código final passou em **252 testes** (220,938 s).

O pacote final passou nas 12 verificações reais: Tk/geometria, download paralelo,
Native Messaging/framing/caixa durável/início desktop, TLS recusando certificado não
confiável, HLS/DASH com FFmpeg e integridade, e 28 MiB com oito conexões/16 intervalos.
Os cinco arquivos de testes Node da extensão passaram; são simulações das APIs do
navegador, não prova de clique real no Chrome/Edge. Há nova rodada manual pendente.

No episódio “De Sururu no Castelo”, mesma variante 2880×2160, o executável final
concluiu em 64,9456 s e 60,4241 s até a publicação; a baseline 1.4 levou
115,2185 s até a mesma indicação de fila. A segunda repetição final incluiu também
a limpeza: 62,6859 s, com 2,188 s medidos na limpeza e diretório removido. CPU da
família observada 40,71875 s, pico RSS concorrente amostrado 107.520.000 B; cinco
processos observados e nenhum erro de coleta. Amostragem pode perder picos/filhos
curtos. A primeira candidata 1.5 com
faststart levou 73,7573 s. São execuções sequenciais com cache do sistema inicializado;
não há isolamento de cache frio, CDN ou condições de rede. Todos os pacotes comprimidos
de vídeo e áudio coincidem com a referência IDM; contêineres têm SHA-256 diferente.
Saída final 2.232.294.941 B, 1253,184 s, H.264 e AAC, 30.045 quadros de vídeo.
A verificação SHA-256 independente coincidiu com a fila e os temporários foram removidos.

O novo benchmark controlado realizou 18 transferências, três por aplicativo/cenário,
com todos os hashes corretos. Medianas completas IDM/Lume: 64 MiB sem limitação
2,1867/3,6507 s; 32 MiB assimétrico 3,7354/4,0594 s; 16 MiB sem Range e 4 MiB/s
5,6785/5,7874 s. Inclui inicialização CLI Lume e ponte COM/PowerShell IDM residente;
não mede apenas os motores. As diferenças de CPU/memória dos fluxos não são equivalentes.

Após recaptura pelo painel IDM, a nova repetição nativa do mesmo episódio 2160p
concluiu em **59,2879 s**, com saída final exclusivamente legível e SHA-256 idêntico
à captura anterior. Vídeo e áudio comprimidos foram comparados diretamente com o
Lume e coincidem. O Lume 1.5 ficou cerca de 2–10% acima desse tempo até publicação.
O tempo IDM antigo de 45,4047 s é histórico. São execuções em horários diferentes,
com cache, rede e atividade do usuário não controlados; IDM residente, Lume com
inicialização e verificação próprias. CPU IDM observada 66,03125 s e pico RSS
amostrado 70.324.224 B, um processo observado. Não houve alteração no executável;
esta revisão acrescenta o resultado atual aos documentos e ao ZIP.

Recibos: `.cache/package-verification.json`, `.cache/applied-final-1.5.json` e
`../.cache/lume-evolution-1.5/`. Veja `docs/DESEMPENHO.md` para durabilidade do cache,
limites de descritores e MP4 local sem faststart. A atualização assinada ainda não tem
feed/chave de produção; proxy, cookies/sessões, FTP, DRM e suporte universal não foram
adicionados. Não houve commit/push nem publicação externa.

## Histórico da versão anterior

# Validação — Lume Downloads 1.4.0

Windows x64, 20/10/2026. A versão 1.4 foi aplicada às 20:07
(America/Sao_Paulo), após confirmar ausência de processos Lume. A versão 1.3
foi preservada em `.cache/build-backups/LumeDownloads-before-1.4-20261020-200722-067`.
Os hashes instalados coincidem com os executáveis validados. A fila não foi
alterada e os registros existentes dos navegadores foram preservados.
Recibo: `.cache/applied-1.4.json`.

O teste pós-instalação confirmou versão 1.4/Tk/janelas, framing nativo v3 e
caixa de entrada em perfil temporário isolado, além do caminho do host no
manifesto registrado: `.cache/installed-1.4-smoke.json`.

Na rodada manual da 1.4, **Chrome foi confirmado**: o arquivo
`Streaming-eb33c6b5522e.mp4` tem 30.175 bytes, áudio/vídeo e SHA-256 igual à
referência; o servidor registrou todos os segmentos, e o monitor registrou
abertura do executável instalado a partir de zero processos. **Edge segue não
confirmado nesta rodada**: somente acesso à página, sem manifesto/segmentos de
referência. O endereço `/edge` sem barra final afetava as URLs relativas da
fixture; o helper recebeu redirecionamento canônico e passou em verificação
isolada. A instância atual do servidor de teste não foi alterada.

Também foi analisado `Streaming-0ad109ab5d86.mp4`: 2.232.294.941 bytes, H.264
2880 × 2160, AAC estéreo e 20 min 53,184 s. A decodificação completa das duas
faixas passou sem erros; tamanho/data permaneceram estáveis. A origem e o
navegador desse arquivo não foram estabelecidos, nem há hash remoto para
comparação. Relatório e recibo: `.cache/streaming-live-1.4/ANALISE-DOWNLOADS.md`
e `downloads-analysis.json`. As comprovações de ambos os navegadores na versão
1.3 continuam abaixo, explicitamente identificadas.

## Evolução de desempenho

Verificação final: **209 testes passaram em 124,341 s**
(`.cache/validation-1.4-tests-final.txt`); cinco suítes Node e quatro verificações
sintáticas da extensão passaram. As **12 verificações dos executáveis** passaram
(`.cache/package-1.4-stage-validation.txt`, `.cache/package-verification.json`).
O download empacotado adicional tem 28 MiB, 14 intervalos e oito conexões TCP,
SHA-256 independente e limpeza da pasta pertencente à tarefa. Regressões finais
de pool TLS (20), streaming (28) e fila pública (8) também passaram.

Hashes SHA-256 dos executáveis validados em staging:

- GUI: `812ba4c86ab675423ad203e18354740c97a1f3b85f333a2e84a388b0f4641253`.
- Host: `be90ed515e0d2927dd0c2001f90a106fe1a636eed7b9cce82029bd3dbee17baf`.

- Transporte HTTP/HTTPS com reutilização de conexões por origem; resposta
  incompleta, erro de validação ou cancelamento descarta a conexão. Teto do
  Manager: 8 conexões compartilhadas por servidor; pool: 32 conexões ociosas.
- Arquivos grandes usam blocos com alvo de 2 MiB, até 128 blocos persistidos.
  Workers livres recebem o maior bloco pendente; o mapa permanece imutável
  durante a transferência e a retomada. Não se corta um pedido HTTP em andamento.
- HLS/DASH transfere recursos independentes em paralelo, conforme a escolha
  de 1 a 8 conexões; intervalos do mesmo recurso preservam um dono serial.
- Extensão 0.3.1 exibe a versão carregada e o resultado da busca no popup.
  Permissões, ID, descoberta e protocolo não foram ampliados.
- O teste empacotado de 28 MiB revelou que a limpeza antiga admitia somente oito
  arquivos de partes. A criação, retomada e limpeza agora compartilham o dono
  do formato dos nomes temporários. O teste passou a exigir saída sem avisos e
  ausência de pastas temporárias após concluir.
- Saída JSON do comando de download usa escapes JSON portáteis, preservando
  nomes Unicode mesmo quando o console Windows usa uma página de códigos antiga.

Medições, custos e limitações: `docs/DESEMPENHO.md`. Não houve comparação com
IDM, rede externa ou cache frio do sistema. Os testes reais dos navegadores
abaixo comprovam a versão 1.3; precisam ser distinguidos da versão nova.

---

# Histórico — Lume Downloads 1.3.0

Windows x64, 20/10/2026. Pacote gerado, validado em staging e aplicado às 19:35
(America/Sao_Paulo), depois de confirmar que não havia processos Lume ativos.
A instalação anterior foi preservada em
`.cache/build-backups/LumeDownloads-before-1.3-20261020-193540-076`.
Os hashes aplicados coincidem com o pacote executado nos testes. Nenhuma
fila/download do usuário foi substituído. Recibo: `.cache/applied-1.3.json`.

## Verificações atuais

- **182 testes passaram em 96,944 s**: `.cache/validation-1.3-tests.txt`.
- **11 verificações dos executáveis passaram**: `.cache/package-1.3-stage-validation.txt`
  e `.cache/package-verification.json`. Incluem Tk/SQLite, cinco janelas e limites
  de botões, framing nativo legado/v2/v3, ausência de HTTP em item mantido na fila,
  recusa TLS, transferências HLS/DASH completas e SHA-256/ffprobe independentes.
  O teste v3 usa o host empacotado seguido do Manager fonte; os downloads HLS/DASH
  por CLI executam o motor dentro do binário empacotado. Não simulam Chrome/Edge.
- Quatro suítes Node e quatro verificações sintáticas passaram para a extensão
  **0.3.0**, com ID preservado. DOM/APIs são simulados: isso não comprova o botão
  real depois de recarregar a extensão.
- Oito testes do fluxo público da fila verificam confirmação persistente, fila
  sem iniciar, continuação, cópias, pausa/reinício, áudio/vídeo separados, somente
  áudio, falha de reconhecimento da publicação e limpeza com nova tentativa.
- O módulo de streaming tem 23 testes com HTTP real/FFmpeg local. A revisão
  independente reproduziu e verificou duas correções: identidade entre intervalos
  do mesmo recurso com If-Range e recusa de tags de criptografia HLS indentadas.
  Cancelamento durante limpeza final e retenção de posse após falha do processo
  também foram exercitados.

Executáveis aplicados (SHA-256):

- GUI: `1d705ca18cc7b6b786f54467a94de327de279777d7c3bd5a55c91c43a12debb6`.
- Host: `f060a6629326d42a616f39a9943cc3cd3f7b48a92944a29cfb8dba5a572fba13`.

## Caso indicado e fontes públicas

### Retorno dos testes manuais em Chrome e Edge

O usuário confirmou conclusão em Chrome e, após recarregar a extensão, em Edge.
O segundo player fica dentro de duas raízes de Shadow DOM aberto. Os pedidos
de manifests e de todos os segmentos foram registrados separadamente para
`/chrome/` e `/edge/`. A conferência independente localizou os arquivos em
`D:\Users\DTI\Downloads`: `Streaming-219529b9fa50.mp4`, sua cópia `(1)` e
`Streaming-bfb0d23be2f4.mp4`. Todos têm 30.175 bytes, vídeo H.264, áudio AAC e
duração de 3,029333 s. Seus hashes SHA-256 coincidem com a referência:
`56ffd62c0da37734deb05605670615ec44814554c71ff6a1d5052a3bbd1df955`.
O monitor registrou aberturas a partir de zero processos; as amostras não
atribuem uma abertura independente a cada navegador. Recibo com URLs, horários
e arquivos: `.cache/streaming-live-1.3/validation.json`.

Em `https://scoobyplus.site/play/watch?v=1776023809367`, a inspeção do DOM
confirmou `VIDEO-MINIMAL-SKIN` → Shadow DOM aberto → `HLSJS-VIDEO` → Shadow DOM
aberto → `VIDEO` com fonte blob. A fonte declarada no componente usa uma URL
HTTP(S) sem extensão. O novo leitor consultou essa fonte e interpretou HLS com
**300 segmentos de vídeo + 300 de áudio**, sem buscar segmentos ou chaves.
Recibo: `.cache/site-streaming-metadata.json`.
Uma segunda consulta repetiu o parsing sem Referer, com os mesmos cabeçalhos
usados pelo envio v3 da extensão, e também encontrou as duas faixas/600
segmentos: `.cache/site-streaming-metadata-no-referrer.json`.

A extensão agora percorre Shadow DOM aberto e reconhece a fonte declarada por
esses componentes. Uma regressão simula exatamente essa estrutura e verifica
o envio v3 com indicação HLS. O botão e o download completo nesse site ainda
precisam de homologação real depois da troca do executável e da extensão.

Catálogos reais do Vimeo (12 opções) e Internet Archive (uma opção) foram
consultados sem login/cookies: `.cache/platform-catalogues-1.3/`. A tentativa
completa da variante Vimeo de 270p foi **recusada por declaração de criptografia**
durante o parsing, com zero bytes de mídia. Não se afirma transferência Vimeo
concluída. Dailymotion ainda depende de compatibilidade/impersonação ausente;
o erro é explícito, sem instalação automática ou exportação de sessão.

## Cobertura e limites

HLS VOD: master/media, TS e fMP4/MAP, intervalos e áudio separado. DASH: MPD
estático de um período, SegmentTemplate/Timeline e SegmentList. O FFmpeg existente
lê apenas os arquivos locais baixados pelo transporte TLS do Lume; a saída é
conferida antes da publicação. Não há recodificação.

Não há cobertura universal. Permanecem sem suporte: DRM/criptografia HLS,
lives, descontinuidades, DASH multiperíodo/SegmentBase-SIDX, login/cookies,
Shadow DOM fechado, fontes sem evidência de protocolo e codecs incompatíveis
com o contêiner de saída. Capturas de Resource Timing sem associação ao player
exigem escolha explícita; não se supõe que sejam o vídeo principal. Detalhes
de limites e posse estão em `docs/STREAMING.md`.

## Medição da família de processos

Motor fonte + SQLite + servidor loopback + FFmpeg/ffprobe, vídeo/áudio gerado de
três segundos. Primeira/segunda tarefa no mesmo processo já inicializado; não
são execuções com cache frio do sistema. CPU inclui o processo e seus filhos,
medida por handles de processo retidos. Memória é o maior RSS simultâneo
**amostrado a cada 5 ms**, uma estimativa que pode perder picos mais curtos.

| Protocolo | Execução | Tempo | CPU da família | Pico RSS amostrado da família |
| --- | --- | ---: | ---: | ---: |
| HLS | primeira | 0,5157 s | 0,1406 s | 58.335.232 B |
| HLS | segunda | 0,4722 s | 0,2188 s | 58.257.408 B |
| DASH | primeira | 0,6558 s | 0,2500 s | 57.303.040 B |
| DASH | segunda | 0,5771 s | 0,1719 s | 57.266.176 B |

Recibo: `.cache/benchmark-1.3.0.json`. As saídas tiveram hashes iguais entre
repetições de cada protocolo. A versão 1.2 não concluía essas tarefas; não existe
comparação equivalente antes/depois. Tokens do agente, GUI/navegadores, variação
de fonte remota/rede e cache frio do sistema não foram medidos.

---

# Histórico — validação Lume Downloads 1.2.0

Verificação em Windows x64, 20/10/2026. O pacote 1.2.0 foi aplicado depois de
confirmar o fechamento do Lume. A versão anterior foi preservada em
`.cache/build-backups/LumeDownloads-before-1.2-20261020-182011-669`.

## Implementação e verificações atuais

- **140 testes passaram em 67,798 s**: `.cache/tests-1.2-release.txt`.
- **Oito verificações do pacote passaram**: inicialização Tk/SQLite, limites dos
  botões em cinco janelas, recursos EJS, HTTP paralelo com hash, framing real do
  host, reutilização de perfil, importação persistente, protocolo v2 com título
  Unicode e mídia mantida sem HTTP em espera, e recusa de TLS inválido.
  Recibo: `.cache/package-verification.json`. O iniciador desses testes foi um
  runner; isso não substitui o envio pela extensão real.
- Pedidos do navegador ficam persistidos até confirmar ou cancelar. As
  regressões verificam ausência de transferência antes da confirmação,
  **Só adicionar à fila** sem iniciar, **Continuar**, repetição idempotente de
  mensagens e cópias com sufixo sem sobrescrever o arquivo anterior.
- A admissão do pedido e da tarefa ocorre em uma transação. Regressões cobrem
  confirmação concorrente, falha de confirmação do commit e cancelamento após
  recuperação. A migração cria backup SQLite no contexto do aplicativo antes
  de adicionar o novo esquema; não substitui a fila existente.
- Os diálogos incluem nome, pasta, destino de duplicata e seleção de formato.
  Testes Tk verificam ações visíveis, contraste e layout em 125%/150% de DPI.
- YouTube: catálogo real de 23 opções, com MP4, WebM e áudio, e probe limitado
  de áudio: `.cache/media-catalog-1.2.json`. Uma transferência completa real de
  **audio:139** concluiu 1.774.414 bytes em 4,484 s; SHA-256 independente
  `a315230d48e6814c847743887e9dc331dafa558e5032bd509c8278f4daf2f81a`.
  FFprobe confirmou AAC, 290,853152 s. Recibo:
  `.cache/youtube-selected-audio-1.2/validation.json`. Esse teste usou o motor
  fonte e um perfil isolado, sem navegador ou GUI empacotada. A integração local
  também transferiu e uniu faixas WebM, verificadas por FFprobe.

Executáveis aplicados (SHA-256):

- GUI: `837400659c22bf8b5c90a704e1c3e7247c202956b72c9b7ea1441e795ef55a2d`.
- Host: `3defc76ef0516eb7e9980d6cbd7f4223889be91f86f9a1107d762aae9d56ff60`.

## Extensão 0.2.1 e validação real pendente

O ID permanece `meicfjedeegbopmeifconaiekbmppbai`. Os registros Chrome/Edge e o
caminho do host permanecem os já configurados. Os botões de vídeo/áudio usam
varredura manual da página ou autorização automática opcional por site. O
catálogo de formatos é consultado no Lume; a extensão não envia cookies.

Três suítes Node passaram: interpretação compartilhada, handoff/permissões e
ciclo do content script. Cobrem revogação em duas abas, documento de iframe,
Unicode, limites, clique confiável e BFCache. As APIs do navegador e o DOM são
simulados; isso não comprova a extensão carregada nem os botões reais. Os quatro
arquivos JavaScript de produção passaram na verificação de sintaxe.

Uma revisão independente reproduziu a revogação nas duas abas: relatórios/envios
posteriores foram recusados, sem chamadas nativas adicionais. O título de 255
letras mais emoji preservou 256 pontos de código e UTF-8 válido. A expansão e
retenção de coleções JavaScript são limitadas; `querySelectorAll` ainda realiza
a busca nativa no DOM. O custo dessa busca não foi medido.

Retorno do usuário após a entrega: o menu não aparece no Chrome; no Edge,
funciona segundo seu relato. O log desta rodada ainda não mostrou requisições
do motor Lume para as fixtures, portanto não se considera o download concluído
nos navegadores. A causa específica do Chrome ainda não foi confirmada.

Foi reproduzida uma fragilidade independente na API simulada: com menu ausente
e evento de startup sem evento de instalação, a extensão 0.2.0 não o recupera.
A extensão 0.2.1 confere/cria o menu no startup e pelo popup, atualiza um item
existente sem remoção e confere erros no callback de criação. A regressão falhou
antes da mudança e passou depois. O popup mostra a confirmação da configuração
ou o erro atual da API. A correção ainda exige recarregar a extensão e retestar
o Chrome real; recuperação simulada não comprova a causa do relato do usuário.

O reteste real da versão 1.2 com extensão recarregada ainda precisa confirmar
Chrome e Edge, abertura com aplicativo fechado, confirmação antes de transferir,
cópia repetida e botão sobre o vídeo. A página desta rodada é
`http://127.0.0.1:52231/`, com nomes terminando em `20261020-181821`.
Recibos ficam em `.cache/evolution-live/`. Os testes anteriores da versão 1.1
abaixo são históricos e não comprovam os novos controles da extensão.

## Custos medidos e limites

Fixture de 2 MiB; um processo inclui motor fonte, SQLite e servidor, sem
GUI/navegadores/filhos. Primeira/segunda transferência em um processo já
inicializado, sem alegação de cache frio do sistema operacional. A confirmação
é programática e imediata; espera humana fica excluída.

| Fluxo | Execução | Tempo | CPU | Pico de memória do processo/família sem filhos |
| --- | --- | ---: | ---: | ---: |
| Direto | primeira | 0,4187 s | 0,0938 s | 38.973.440 B |
| Direto | segunda | 0,3102 s | 0,0312 s | 40.419.328 B |
| Pedido confirmado | primeira | 0,4319 s | 0,0781 s | 40.419.328 B |
| Pedido confirmado | segunda | 0,4762 s | 0,0469 s | 40.419.328 B |

Recibo: `.cache/benchmark-1.2.0.json`. Tokens do agente, custo da GUI/navegadores,
família Node/FFmpeg e variação da extração remota não foram medidos. A rodada
histórica 1.1 ocorreu em outro momento, sem comparação controlada antes/depois.

Não há promessa de baixar de qualquer site. Nesta versão: arquivos diretos
HTTP/HTTPS, mídia direta reconhecida pelo player e vídeos públicos compatíveis
do YouTube. DRM, login/cookies, lives, HLS/DASH genérico e conversão entre
formatos não foram implementados/homologados. MP4/WebM/M4A são opções disponíveis
na origem, sem recodificação. Etapas futuras constam no plano de evolução.

---

# Histórico — validação Lume Downloads 1.1.0

Verificação atual em Windows x64, 20/10/2026:

- **101 testes passaram**, 48,072 s. Recibo: `.cache/build-1.1.0-final.txt`.
- **Sete verificações do pacote final passaram**. O executável mediu altura,
  largura, visibilidade e limites dos botões da janela principal e dos três
  diálogos; também confirmou recursos EJS. Recibo: `.cache/package-verification.json`.
- Regressões em Tk cobrem 10 combinações de tamanho/DPI (125%/150%) e contraste
  normal/desabilitado de pelo menos 4,5:1. A janela real corrigida foi observada.
- Worker Node passou com API simulada; isso não prova integração no navegador.
- `File.Replace` foi exercitado no PowerShell 5 com `[NullString]::Value`.
- O build agora gera em staging, valida e só aplica com o Lume fechado; a
  instalação anterior recebe backup. Isso evita remover arquivos de uma janela
  reaberta pelo usuário durante o empacotamento.

Executáveis aplicados (SHA-256):

- GUI: `6f443048fcb8db958b50bf456386b907f339a1766eeefcade835c01e72d19e8e`.
- Host: `2e1d2c8705ef7a73be45efdded076e4a86f500c0de88648bb0be99f1a1aa7677`.

## YouTube real

O vídeo `aGCdLKXNF3w`, adicionado pelo usuário à interface, foi concluído como
`D:\Users\DTI\Downloads\YouTube-aGCdLKXNF3w.mp4`, com 29.812.328 bytes.
Uma verificação independente confirmou vídeo AV1 1920×1080, áudio AAC e duração
de 290,783492 s. SHA-256 igual ao da fila:
`8f8c8a560582effc5c324457da5bb17768fe9c9a186fc8bd865494cbc00990b3`.
Recibo: `.cache/youtube-live-verified.json`.

O resolver real confirmou formatos `399+140` e HTTP 206 em ambas as faixas.
O motor transfere as faixas e o FFmpeg existente as reúne. HTML no lugar de mídia
é recusado antes da publicação. Login/cookies, DRM, lives e HLS não foram homologados.

## Host e navegadores reais

O host final foi invocado por framing real: abriu o executável gráfico, importou
um pedido e concluiu uma fixture de 2 MiB em 2,103 s, com comparação de SHA-256.
O teste prova host → abertura automática → fila → arquivo; seu iniciador foi um
runner, não Chrome/Edge. Recibo: `.cache/native-autostart-live.json`.

O usuário carregou as extensões Chrome/Edge com ID
`meicfjedeegbopmeifconaiekbmppbai`, conferido nos perfis.
O Chrome retornou `Specified native messaging host not found`.
Foi observado redirecionamento do AppData para o LocalCache privado do Codex.
O manifesto foi migrado para o caminho real
`dist/BrowserHost/app.lume.downloads.json`, atualizando os dois registros HKCU,
preservando origens e guardando os valores anteriores em
`dist/BrowserHost/backups/20261020-161309-543-0deb368defc347188b23257358f50ea4`.
Nenhuma política do navegador foi alterada. Filas normal e virtualizada foram
mantidas separadas; backups não substituíram a fila do usuário.

**Reteste após a migração: downloads confirmados.** O usuário confirmou que
funcionou ao testar os links correspondentes no Chrome e no Edge. Os dois
arquivos `lume-chrome-20261020-170658.bin` e `lume-edge-20261020-170658.bin`,
em `D:\Users\DTI\Downloads`, têm 2.097.152 bytes e SHA-256
`91d3beb88a9b2f778a6c44a1c53b63d3c79931845a9aef84b3fb414610bd1938`,
iguais à fixture independente.

O servidor registrou, para cada URL, o probe `bytes=0-0` e quatro intervalos
de transferência com User-Agent `LumeDownloads/1.0` (identificação HTTP do motor,
não a versão do pacote). Isso distingue os downloads pelo Lume de um GET comum
do navegador. A atribuição de cada envio ao navegador vem do relato do usuário
e do link específico usado; o servidor recebe conexões do aplicativo.
Recibo: `.cache/browser-live-final/validation.json`.

A leitura da fila pelo processo de diagnóstico foi redirecionada pelo Windows
para o LocalCache privado do Codex, portanto os IDs/estados da fila normal não
foram inspecionados diretamente nesse reteste. Também não foi isolada uma nova
inicialização com o Lume fechado para cada navegador; a abertura automática
continua comprovada pelo teste do host descrito acima, além do relato de sucesso
do usuário no fluxo real. Nenhum download de teste foi apagado.

Na conferência posterior, `NtQueryKey` confirmou que as chaves HKCU de ambos os
navegadores apontam para `\REGISTRY\USER\<SID do usuário>\Software\...`, sem
redirecionamento dessas chaves. As duas visões do registro, o JSON do manifesto
e a existência do executável foram conferidos. Isso não demonstra que o processo
do navegador encontrou e iniciou o host. O popup reapresenta o último resultado
salvo, portanto a mensagem após um envio novo é necessária para distinguir
falha atual de um aviso histórico. Nenhuma política foi modificada.

## Medição atual

Fixture IPv4 de 2 MiB; processo inclui motor, SQLite e servidor, sem GUI/filhos.
Primeira/segunda transferência por política não representam cache de SO frio.

| Checkpoint | Execução | Tempo | CPU | Pico de memória do processo/família sem filhos |
| --- | --- | ---: | ---: | ---: |
| 64 KiB | primeira | 0,4292 s | 0,0938 s | 39.411.712 B |
| 64 KiB | segunda | 0,2336 s | 0,0625 s | 40.235.008 B |
| 1 MiB | primeira | 0,3285 s | 0,0625 s | 40.460.288 B |
| 1 MiB | segunda | 0,3266 s | 0,1094 s | 40.460.288 B |

Não demonstra melhora universal. CPU/memória da família Node/FFmpeg no YouTube
real e tokens do agente não foram medidos. Recibo: `.cache/benchmark.json`.

## Registro histórico — versão 1.0.0 (superado pelo estado acima)

Executada em Windows x64 em 20/10/2026, com Python 3.12.10 do ambiente isolado do
projeto. Nenhum teste instalou, carregou ou executou o IDM.

## Evidência integrada

- `python -m unittest discover -s tests -v`: **65 testes passaram**, 40,498 s, exit 0.
- `python tools/verify_package.py`: **cinco verificações do pacote passaram**, exit 0.
- `node browser-extension/tests/native-handoff.test.cjs`: teste do worker passou
  com API do navegador simulada. Sintaxe dos dois arquivos JavaScript validada.
- Script PowerShell de registro: análise sintática passou; nenhum registro foi escrito.
- `python -m compileall -q lume tools tests`: passou.

Os logs e recibos completos ficam em `.cache/integrated-tests.txt`,
`.cache/package-verification.json`, `.cache/final-build.txt`, `.cache/benchmark.json`
e `.cache/release.json`. As dependências exatas ficam em `requirements-lock.txt`.

## Cobertura

| Comportamento | Resultado observado |
| --- | --- |
| Download paralelo | Conteúdo final comparado byte a byte e por SHA-256 com fixture independente |
| Pausa, cancelamento e reabertura | Checkpoints duráveis; retomada exata após recriar o Manager |
| Alteração de recurso/intervalo/tamanho | Falha explícita; nenhum arquivo final incompatível publicado |
| Servidor sem Range ou que o ignora | Conexão única/fallback seguro antes de dados confirmados |
| TLS | Certificado não confiável e hostname incompatível recusados; certificado de teste confiável aceito |
| Redirecionamento | Seguimento controlado, limite de loops e recusa de HTTPS para HTTP |
| Destino existente | Preservado, inclusive quando aparece durante a transferência/finalização |
| Recuperação de publicação | Intenção persistida antes de rename; reconciliação por tamanho/hash sem rede após falha do commit final |
| Limpeza com falha | Ownership retido no SQLite e nova tentativa permitida |
| Fila/agendamento/velocidade | Persistência, início posterior ao agendamento e limite de transferência exercitados |
| Entrada de navegador | Protocolo real do executável, publicação em disco e importação única pelo motor |
| Entrada inválida/interrompida | Scheduler continua ativo; claim anterior ao commit é recuperado |
| Interface Tk | Ações, lotes, seleção, preferências, integração real com Manager e encerramento assíncrono |
| Ciclo de vida Tk/threads | Regressão força coleta em worker após cinco ciclos de destruição de janelas; sem crash |
| Atualização assinada | Ed25519, hash, tamanho, versão, chave ausente/incorreta e adulteração exercitados |
| Extração de atualização | Recusa de traversal, symlink, nomes Windows, colisões, quotas e diretório ZIP forjado |

## Pacote Windows

`LumeDownloads.exe` é o aplicativo gráfico; `LumeNativeHost.exe` é o host com
stdin/stdout. O teste do pacote abriu/fechou Tk e SQLite, baixou um arquivo com
o próprio executável, conferiu conteúdo/hash, enviou uma mensagem pelo host
empacotado até o motor e confirmou a recusa de um certificado não confiável.
Os hashes dos dois executáveis estão no recibo de verificação do pacote.

## Medição de tarefa

Benchmark controlado, quatro partes, arquivo de 2 MiB, mesmo processo/perfil,
servidor IPv4 de loopback. O processo medido inclui o motor, SQLite e o servidor
de teste; não inclui GUI ou processos filhos. Primeira/segunda transferência
por política são chamadas de cold/warm no recibo; não significam cache de SO frio.

| Checkpoint | Execução | Tempo | CPU | Pico working set do processo |
| --- | --- | ---: | ---: | ---: |
| 64 KiB | primeira | 0,3499 s | 0,0469 s | 39.591.936 B |
| 64 KiB | segunda | 0,3324 s | 0,1250 s | 40.173.568 B |
| 1 MiB | primeira | 0,2680 s | 0,0156 s | 40.386.560 B |
| 1 MiB | segunda | 0,3176 s | 0,0781 s | 40.550.400 B |

É uma comparação pequena de política de checkpoint, não promessa de velocidade
da internet nem prova de melhora universal. A política de 1 MiB reduz commits
mantendo fsync antes do checkpoint e ao pausar/finalizar; uma interrupção abrupta
repete somente o trecho ainda não confirmado. Pico de memória é cumulativo no
processo. Não há baseline de aplicativo anterior: este projeto é novo. Uso de
tokens do agente não foi medido; não há contador de consumo disponível.

## Limites e configuração opcional

- Chrome/Edge reais não foram registrados ou alterados. A extensão e o host estão
  implementados; instalação manual, IDs e políticas corporativas exigem configuração local.
- Não há chave ou servidor de releases de produção. O cliente verifica/prepara
  um pacote fornecido com chave pública confiável; não instala automaticamente.
- Agendamento não acorda o computador e depende do aplicativo aberto.
- Falta de espaço foi exercitada no limite de capacidade informado pelo sistema;
  não se preencheu um disco real. Não é uma auditoria completa do Windows/filesystem.
- Testes de rede foram locais. Não foram feitas medições de velocidade contra
  serviços externos, nem testes com servidores autenticados/proxies/cookies.
# OpenDownloader 1.13.0 — evidências de evolução

A suíte integrada desta entrega executou 526 testes: 522 aprovados e quatro
ignorados. A extensão 0.6.0 executou 34 testes JavaScript aprovados. Os testes de
streaming incluem análise antecipada de MP4 protegido, recuperação limitada de
segmentos, seleção de idioma de áudio e WebVTT em HLS/DASH, com texto extraído
independentemente do MP4 final. Fontes sintéticas não comprovam compatibilidade
com catálogos comerciais protegidos.

O ensaio no Chrome com mídia sintética gerou recorte de 320×180, H.264/AAC,
94 quadros, áudio não silencioso e decodificação independente completa. Não usa
o seletor de compartilhamento nem substitui a prova da ponte da extensão.

A matriz final do instalador, componentes privados, instalação e interação real
com Chrome/Edge está no relatório de entrega `OpenDownloaderSetup-1.13.0-VALIDACAO.md`
da pasta `dist`; resultados anteriores abaixo mantêm sua data e escopo históricos.

