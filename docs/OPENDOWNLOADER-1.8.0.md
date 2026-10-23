# OpenDownloader 1.8.0 — interface e instalação local

## Instalação

Use `OpenDownloaderSetup-1.8.0-windows-x64.exe`. O pacote reúne aplicativo,
Python, Node.js e FFmpeg/ffprobe privados, ponte local, extensão e atalhos.
Os componentes privados mantêm os arquivos e hashes aprovados na versão 1.7.1.
Não há instalação global de runtimes ou alteração do PATH.

A conclusão abre o assistente dos navegadores. Escolha Chrome ou Edge, copie a
pasta indicada e abra o gerenciador de extensões correspondente. Ative Modo do
desenvolvedor e use Carregar sem compactação uma vez em cada navegador. Depois
use Verificar conexão com o aplicativo no popup e faça um envio de teste.
Nas atualizações da extensão local, use Recarregar no navegador.

Ponte pronta, caminho copiado e solicitação de abertura são estados distintos.
Nenhum deles comprova que a extensão foi carregada/ativada ou que houve download.
A publicação nas lojas e o anúncio externo continuam suspensos.

## Interface

A organização foi revista a partir da referência visual fornecida pelo usuário:
ações de download próximas da lista, navegação por categoria/estado à esquerda,
busca e filas acessíveis, e detalhes do item selecionado. São usados o motor e
as funções existentes; a referência visual não implica paridade com todos os
recursos do IDM ou suporte a todos os sites e formatos.

Os nomes de executáveis, pastas de dados e host `app.lume.downloads` permanecem
compatíveis. A atualização conserva fila e arquivos finais. Feche o aplicativo
antes de aplicar o novo instalador; a instalação anterior é preservada em backup.

## Verificação da entrega

O relatório final fica ao lado do instalador, em
`OpenDownloaderSetup-1.8.0-VALIDACAO.md`. Ele identifica os testes executados,
o SHA-256 do pacote e as limitações da rodada. O histórico em `VALIDACAO.md`
corresponde a versões anteriores e não substitui essa evidência.
