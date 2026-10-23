# OpenDownloader 1.8.1 — busca de mídia em páginas com frames mistos

Extensão local 0.3.5, com mesma chave, ID e permissões. Nenhuma publicação em loja.

## Diagnóstico

O usuário informou que Procurar nesta página retornava “Use um link HTTP ou HTTPS”
na HBO Max. A imagem mostra extensão em execução 0.3.1. A instalação lida neste
computador já contém aplicativo 1.8.0/extensão 0.3.4; portanto o navegador ainda
está executando outra cópia/estado anterior, cujo caminho não foi identificado.

A inspeção somente leitura da página aberta encontrou um player blob e frames
about/javascript/HTTPS. A presença de blob, ou a ausência momentânea de mediaKeys,
não comprova que o episódio tenha ou não criptografia/DRM.

A origem do erro foi reproduzida no fluxo de eventos simulado: a busca percorria
os frames retornados pelo navegador e validava about:blank como se fosse uma URL
de download. Uma exceção abortava toda a busca, inclusive os frames HTTP válidos.

## Correção

As identidades de frames elegíveis são interpretadas antes da gravação de estado.
Somente documentos HTTP(S) identificados recebem autorização; os demais são
ignorados sem herdar a origem do pai. A injeção manual fica limitada aos IDs de
documento confirmados. A confirmação de propriedade após navegação é preservada.

O popup distingue falha de busca de mídia de falha no envio de um link direto.
Não há extração de chaves, quebra de DRM, importação de cookies ou sessões.

## Aplicação local

Execute o instalador 1.8.1 com o aplicativo fechado. No assistente, copie a pasta
da extensão instalada (`app/browser-extension`). Se o navegador continuar em
0.3.1, apenas recarregar a cópia antiga não troca seus arquivos: remova a cópia
antiga pelo gerenciador e carregue a pasta indicada pelo assistente. O ID continua
`meicfjedeegbopmeifconaiekbmppbai`. Confira **Extensão 0.3.5** no popup e então
repita Procurar nesta página. Em seguida selecione uma fonte compatível.

## Limitações

Corrigir a busca não comprova que o episódio completo possa ser baixado ou
reproduzido. Fontes observadas podem ser anúncios, segmentos ou faixas separadas.
Fluxos protegidos por DRM/criptografia continuam fora do suporte. Nenhum episódio
da HBO/Netflix foi baixado nesta rodada. O relatório ao lado do instalador distingue
as regressões simuladas, os testes reais do pacote e a validação ainda pendente
no popup dos navegadores.

Fonte oficial sobre a limitação equivalente do IDM:
https://www.internetdownloadmanager.com/register/new_faq/video11.html
