# OpenDownloader

O produto passou a se chamar **OpenDownloader** na versão 1.7.2. Os nomes internos
`LumeDownloads`, `LumeNativeHost.exe` e `app.lume.downloads` continuam por
compatibilidade com a fila e as extensões existentes. Veja [a mudança de nome](docs/OPENDOWNLOADER-1.7.2.md).

Gerenciador de downloads local para Windows, com interface em português e código autoral.
Funciona sem instalar ou executar o IDM. Não usa suas DLLs, drivers ou código.

## Baixar e instalar (Windows x64)

**[Baixar o instalador do OpenDownloader 1.13.0](https://github.com/vthamada/opendownloader/releases/download/v1.13.0/OpenDownloaderSetup-1.13.0-windows-x64.exe)**

[Notas da release](https://github.com/vthamada/opendownloader/releases/tag/v1.13.0)
· [Arquivo SHA-256 para conferir a integridade](https://github.com/vthamada/opendownloader/releases/download/v1.13.0/OpenDownloaderSetup-1.13.0-windows-x64.sha256).

1. Baixe o instalador pelo link acima. Se estiver atualizando, feche o OpenDownloader antes de continuar.
2. Execute `OpenDownloaderSetup-1.13.0-windows-x64.exe`, confirme a pasta e conclua o assistente.
   A pasta padrão é `%LOCALAPPDATA%\Programs\LumeDownloads`.
3. Abra o **OpenDownloader** pelo atalho criado no menu Iniciar.
4. Para conectar o navegador, use a tela **Conectar navegadores** do assistente ou
   o menu **Navegadores → Conectar Chrome e Edge**. Selecione Chrome ou Edge e copie
   o caminho da extensão mostrado pelo aplicativo. No gerenciador de extensões,
   ative **Modo do desenvolvedor**, escolha **Carregar sem compactação** e selecione essa pasta.
   Faça isso uma vez em cada navegador; após atualizar, recarregue a extensão para a versão 0.6.0.
5. No popup da extensão, clique em **Verificar conexão com o aplicativo**.
   Depois envie um arquivo conhecido, confirme o download no aplicativo e confira sua conclusão.

O instalador inclui Python e os componentes de mídia Node.js e FFmpeg/ffprobe;
não é necessário instalá-los separadamente. A fila e os downloads ficam fora da
pasta de instalação e são preservados na atualização.
Veja o [guia de instalação](docs/DISTRIBUICAO-LOCAL.md) para detalhes.

## Abrir

O instalador OpenDownloader 1.13.0 reúne aplicativo, componentes de vídeo privados,
atalhos, ponte e reparo em um assistente.
O menu **Navegadores → Conectar Chrome e Edge** centraliza a configuração.
Interface e configuração local: [OpenDownloader 1.8.0](docs/OPENDOWNLOADER-1.8.0.md).
Correção de busca em páginas com frames mistos: [OpenDownloader 1.8.1](docs/OPENDOWNLOADER-1.8.1.md).
HLS AES-128 autorizado e limites de criptografia: [OpenDownloader 1.9.0](docs/OPENDOWNLOADER-1.9.0.md).
Correção da atualização e dos erros silenciosos: [OpenDownloader 1.9.1](docs/OPENDOWNLOADER-1.9.1.md).
SAMPLE-AES e CENC com chaves autorizadas: [OpenDownloader 1.10.0](docs/OPENDOWNLOADER-1.10.0.md).
Gravar reprodução no Chrome e Edge: [OpenDownloader 1.11.0](docs/OPENDOWNLOADER-1.11.0.md).
Faixas de áudio HLS, prévia e controles de gravação: [OpenDownloader 1.12.0](docs/OPENDOWNLOADER-1.12.0.md).
Tratamento de MP4 protegido no envio pelo player: [OpenDownloader 1.12.1](docs/OPENDOWNLOADER-1.12.1.md).
Detecção antecipada, recuperação de segmentos, faixas e recorte: [OpenDownloader 1.13.0](docs/OPENDOWNLOADER-1.13.0.md).
Histórico da mudança de nome: [OpenDownloader 1.7.2](docs/OPENDOWNLOADER-1.7.2.md).
Evidências anteriores: [Instalador 1.7.1](docs/INSTALADOR-1.7.1.md).
Procedimento: [Instalação local](docs/DISTRIBUICAO-LOCAL.md).
O fluxo abaixo continua válido para o pacote portátil anterior preservado.

Execute **`dist/LumeDownloads/LumeDownloads.exe`**, ou dê dois cliques em **`start.cmd`**.
Mantenha o executável junto da pasta `_internal` e de `LumeNativeHost.exe`.
O pacote é portátil: não instala serviços, drivers ou programas no sistema.

Clique em **Novo download**, cole uma ou várias URLs, escolha a pasta e confirme.
Para um único arquivo é possível escolher nome, SHA-256 esperado e agendamento.
Use os controles da fila para pausar, retomar, cancelar, reiniciar ou remover.
**Remover** preserva arquivos finais e limpa somente temporários cuja posse é comprovada.

## Recursos

- Fila persistente em SQLite, com recuperação após fechar/reabrir.
- Filas nomeadas com suspensão de novos inícios, filtro e atribuição de tarefas paradas.
- Pastas sugeridas por categoria, aplicadas explicitamente na confirmação.
- HTTP e HTTPS; TLS verifica cadeia e nome do servidor, com mínimo TLS 1.2.
- Pausa/retomada usando Range/If-Range e ETag forte ou Last-Modified válido.
- De 1 a 8 conexões por arquivo, até 3 downloads simultâneos e teto compartilhado de 8 conexões por servidor.
- Arquivos grandes divididos em blocos de trabalho: conexões livres continuam nos blocos pendentes.
- Reutilização de conexões HTTP/HTTPS válidas, com descarte seguro após falha ou cancelamento.
- Fallback para conexão única quando o servidor não oferece intervalos seguros.
- Recusa de intervalos, tamanhos ou identidade de recurso inconsistentes.
- SHA-256 calculado; comparação com hash esperado opcional.
- Agendamento enquanto o aplicativo está aberto, limite global de velocidade e detalhes da fila.
- Arquivo final publicado somente após verificação, sem sobrescrever destinos existentes.
- Extensão Chrome/Edge com confirmação de nome, destino e ação antes de transferir.
- Duplicatas: nova cópia com sufixo livre, prévia do destino e opção de abrir a pasta do existente.
- Pedidos pendentes persistentes e opção **Só adicionar à fila**, iniciada depois por **Continuar**.
- Links públicos YouTube: catálogo de resoluções, MP4/WebM e somente áudio M4A/WebM.
- HLS/DASH VOD: manifests, segmentos em paralelo, qualidades e faixas separadas.
- HLS AES-128/identity com chave HTTP(S) autorizada, IV explícito/por sequência e rotação; sem extração de chaves DRM.
- HLS SAMPLE-AES/identity e DASH CENC com chaves KID explícitas mantidas somente na sessão; detalhes e limites na documentação 1.10.0.
- Catálogos de páginas públicas compatíveis do Vimeo e Internet Archive; a disponibilidade depende da fonte.
- União de faixas de vídeo e áudio com FFmpeg, sem recodificar.
- Botões em players de vídeo/áudio: procura manual e ativação automática opcional por site.
- Verificação de releases Ed25519 e preparação segura de ZIPs em pasta nova.

Na versão 1.4, a opção **Conexões por arquivo** também controla o paralelismo de
segmentos HLS/DASH. O teto por servidor é compartilhado pela fila. O ganho depende
da fonte, latência, disco e banda disponível. Medições e limites estão em
[DESEMPENHO](docs/DESEMPENHO.md). A extensão 0.3.2 mostra no popup a versão carregada,
o resultado da busca e **Verificar conexão com o aplicativo**. Esse diagnóstico
consulta o host sem abrir o OpenDownloader nem admitir tarefas; não comprova uma transferência.
Depois de atualizar os arquivos, recarregue a extensão no navegador.

Na versão 1.6, use o menu **Fila** para criar uma fila e suspender/liberar novos
inícios. Downloads ativos podem terminar; os limites globais de simultâneos e
conexões continuam compartilhados. Em **Preferências**, defina pastas por categoria;
na confirmação, **Usar pasta da categoria** mostra o destino antes da admissão.
Uma pasta escolhida manualmente continua sendo respeitada. Categorias refletem o
nome do arquivo, não inspeção do conteúdo. Na árvore, Espaço pausa/continua e
Ctrl+Enter abre confirmação/detalhes; os atalhos não atuam em campos de edição.

O aplicativo não executa automaticamente arquivos baixados. Atualizações são preparadas para
revisão e troca manual com o aplicativo fechado; não há servidor de atualização ou chave de
produção preconfigurados. Verifique **Segurança → Preparar atualização assinada** e [UPDATES](docs/UPDATES.md).
A entrega atual usa o instalador Windows e SHA-256, sem identidade de assinatura de produção:
veja [DISTRIBUICAO-LOCAL](docs/DISTRIBUICAO-LOCAL.md).

Se o recurso mudou ou o servidor não permite retomada segura, o OpenDownloader preserva as partes e
solicita **Reiniciar**. Isso evita combinar versões diferentes do arquivo. Uma falha deixa
o item na fila para revisão; o programa não insiste indefinidamente em requisições.

## Dados e limites

Os dados da fila ficam em `%LOCALAPPDATA%/LumeDownloads/queue.sqlite3`.
Temporários ficam na pasta de destino, em `.lume-ID`; não os altere durante a transferência.
Para montar o arquivo final é necessário espaço para as partes e outra cópia do arquivo.
URLs completas ficam na fila local para permitir retomada; a interface omite a query de exibição.
Não são aceitas URLs com usuário/senha embutidos. URLs temporárias podem expirar.

Links `youtube.com/watch`, `youtu.be` e `youtube.com/shorts` são resolvidos antes da transferência.
Use **Consultar formatos** para escolher uma versão disponível. MP4/WebM são
formatos de saída suportados; somente áudio preserva M4A/WebM. Não há conversão
para MP3 nem aumento artificial de resolução. O formato explícito é mantido nas
tentativas seguintes; quando não estiver mais disponível, o item falha com uma explicação.
O instalador local inclui os componentes Node.js e FFmpeg/ffprobe privados já autorizados,
sem modificar o PATH. Ao executar pelo código, são necessários componentes existentes compatíveis.
O formato disponível pode limitar a qualidade. Vídeos que exigem login, transmissões ao vivo,
playlists de vários vídeos e DRM não são suportados neste modo.
Se não houver um arquivo direto disponível, o item falha com uma explicação; uma página HTML
não é marcada como vídeo concluído. Retomar resolve o endereço novamente, mas pode exigir
**Reiniciar** se a identidade do arquivo temporário tiver mudado.
Downloads antigos de páginas HTML são preservados; adicione o link novamente para baixar o vídeo.

HLS `.m3u8` e DASH `.mpd` também podem ser enviados diretamente. A confirmação
lista as opções do manifest, e a transferência reúne segmentos localmente com
FFmpeg e confere as faixas/duração com ffprobe. HLS aceita VOD com TS ou fMP4,
mapas de inicialização, intervalos e áudio separado. DASH aceita MPD estático de
um período, com SegmentTemplate/Timeline ou SegmentList. A retomada exige
manifest/qualidade/identidades compatíveis; caso contrário, use **Reiniciar**.
HLS AES-128/identity, SAMPLE-AES TS H.264/AAC e DASH CENC/CBCS aceitam chaves
autorizadas nos limites documentados. Lives, DASH de vários períodos,
SegmentBase/SIDX, DRM comercial e conversão de codecs continuam sem suporte.
Consulte [STREAMING](docs/STREAMING.md).
O catálogo do Vimeo foi consultado sem login, mas a amostra de transferência
escolhida anunciou criptografia e foi recusada antes de buscar segmentos.
Dailymotion possui roteamento, mas exige um componente de compatibilidade
ausente nesta instalação; o aplicativo informa essa dependência.

Conexões são diretas: não há configuração de proxy, importação de cookies/sessões do navegador,
FTP, captura de streams protegidos, drivers, serviço em segundo plano ou despertar automático
do computador. O agendamento depende da janela aberta e do computador ativo. Esses recursos
não fazem parte do contrato de downloads HTTP(S) implementado aqui.

A integração real com navegador exige configuração opcional: veja
[browser-extension/README.md](browser-extension/README.md). Registre **LumeNativeHost.exe**,
que mantém os streams necessários ao protocolo; o executável gráfico não serve como host.
O instalador registra a ponte por usuário, sem exigir administrador. A extensão
local precisa da ativação explícita no navegador, guiada pelo assistente.
Ao receber um link pela extensão, o host solicita a abertura do OpenDownloader. O pedido
aparece como **Aguardando confirmação**. Confira nome/pasta e escolha **Baixar agora**,
**Só adicionar à fila** ou **Cancelar**. Fechar a janela de confirmação pelo X mantém
o pedido pendente; use **Confirmar…** para reabri-lo. A preferência **Iniciar downloads
do navegador automaticamente** é opcional e fica desativada por padrão.
Pedidos rejeitados exibem o motivo; a confirmação de recebimento pela ponte não
significa que o download terminou. A atualização da fila antiga cria um backup
consistente em `backups/queue-before-1.2-*.sqlite3` na pasta de dados antes da migração.

Para botões nos vídeos, recarregue a extensão atualizada e abra seu popup no site.
Escolha **Procurar nesta página** para acesso temporário, ou **Ativar botões neste site**
para conceder o acesso opcional ao site. Isso não habilita todos os sites automaticamente.
Os botões enviam o pedido à mesma confirmação do aplicativo. A extensão 0.3
alcança players dentro de Shadow DOM aberto e reconhece fontes declaradas por
componentes HLS/DASH, mesmo sem extensão na URL. Também observa URLs de mídia
expostas no Resource Timing da página autorizada. `blob:` é uma referência do
navegador: não se transfere esse endereço; transfere-se a fonte HTTP(S) reconhecida.
Fontes sem associação comprovada ao player aparecem para escolha explícita;
não são atribuídas automaticamente ao vídeo principal. Shadow DOM fechado,
fontes sem evidência de protocolo, frames sem permissão e login/cookies continuam
como limites. Não há promessa de baixar vídeos de qualquer site.

O manifesto do host fica em `dist/BrowserHost` no projeto-fonte ou `BrowserHost`
no pacote portátil. A pasta junto ao aplicativo evita o redirecionamento do
AppData observado ao configurar por um processo MSIX. Os registros são apenas
HKCU; nenhuma política ou proteção do navegador é alterada.

## Executar pelo código

Requisitos: Python 3.12 com Tk, SQLite e SSL; `cryptography` para atualizações assinadas;
`yt-dlp[default]`, um Node.js 22+ existente e FFmpeg/ffprobe para vídeos YouTube com faixas separadas.
As versões usadas no pacote estão em `requirements-lock.txt`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m lume
```

Terminal e entrada de links:

```powershell
.\.venv\Scripts\python.exe -m lume --download 'https://example.org/arquivo.zip' --download-dir 'D:\Downloads' --parts 4
.\.venv\Scripts\python.exe -m lume --add 'https://example.org/arquivo.zip'
```

`--add` confirma somente o recebimento local. Abra o aplicativo para processar a entrada.
`--data-dir` permite um perfil separado; cada perfil aceita uma instância desktop.

## Testes e empacotamento

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\tools\build.ps1 -Python 'C:\caminho\python.exe'
```

O build instala dependências somente em `.venv`, roda os testes e gera os dois executáveis
em `dist/LumeDownloads`. Downloads HTTP(S) diretos não precisam de Node; o suporte ao YouTube
usa um Node.js 22+ existente. Não há instalação de Python/Java/Node por esses scripts.

Os testes usam servidores locais e certificados efêmeros do próprio projeto. Nenhum teste
carrega ou executa o IDM. Veja `VALIDACAO.md` para evidência da execução integrada e limites.

## Novidades da versão 1.5

Menos sincronizações de disco e checkpoints maiores aceleram arquivos diretos.
Streaming HLS/DASH monta segmentos locais sem duplicar as faixas intermediárias
quando está dentro do orçamento de descritores. TLS, identidade na retomada e
verificação final continuam ativos. Veja `docs/DESEMPENHO.md` para limites de cache.

O menu **Fila** importa/exporta listas TXT, move a seleção para cima/baixo e permite
trocar a URL de uma tarefa parada, descartando apenas seus temporários após validação.
Busca, categoria e ordenação ajudam a localizar itens. Importar abre a confirmação;
não inicia downloads sem a escolha do usuário. Exportar preserva URLs e parâmetros,
removendo somente credenciais HTTP embutidas. Guarde a lista conforme o conteúdo.
