# Publicação da extensão — preparação local

Estado em 21/10/2026: **publicação suspensa a pedido do usuário**, após constatar
a taxa de cadastro do Chrome. Não criar contas, aceitar termos, pagar taxas nem
enviar pacotes enquanto essa etapa permanecer suspensa. Nenhuma conta de editor
foi criada, taxa paga, item enviado ou publicação realizada. O programa instalado
continua OpenDownloader 1.7.2 com a extensão 0.3.3; há preparação local da extensão
0.3.4, sem aplicação ou publicação. `browser-distribution.json` permanece
`published:false` nas duas lojas.

O usuário informou que ainda não possui site nem contas de desenvolvedor.
Nome público do responsável, e-mail de suporte e hospedagem não foram definidos.
O painel do Chrome recusou a automação (`Not allowed`); o Partner Center abriu
na tela de login. Não houve tentativa de contornar essa restrição.

## Preparação local preservada

- Ícones originais e pacote da extensão 0.3.4 em `.cache/store-assets/`.
- Textos de listagem em `docs/STORE-LISTING.md`, com dados pendentes explícitos.
- Contrato compartilhado em `lume/distribution.py` valida URLs/IDs dos canais.
- A ponte pode planejar IDs adicionais sem retirar a identidade local.
- O módulo de anúncio de instalação externa é preparação isolada; **não está
  integrado ao instalador de produção nem validado em navegadores reais**.
- Não foi gerado/aplicado outro executável nesta etapa. A instalação 1.7.2 e
  os registros reais dos navegadores permanecem como estavam.

O modelo verificado do IDM é aplicativo Windows + host Native Messaging + extensão
distribuída pela loja. O registro da ponte permite localizar o host; não comprova
instalação/ativação da extensão. No Windows, o fluxo externo suportado do Chrome
usa a Chrome Web Store e pede a confirmação do usuário.

Passaram 34 testes dos contratos de distribuição, da ponte e do assistente.
Isso não comprova anúncio externo, publicação ou instalação da extensão pela loja.

## Material preparado

- Pacote `.cache/store-assets/OpenDownloaderExtension-0.3.4.zip`, com manifest e recursos locais da extensão.
  É um pacote para revisão, sem atestado de aprovação das lojas; não inclui testes,
  executáveis, bases locais ou credenciais.
- Propósito: enviar links escolhidos pelo usuário ao aplicativo local e identificar
  mídia compatível na página mediante ação/permissão explícita.
- Descrição: “Escolha arquivos e vídeos da página e confirme as opções no OpenDownloader.”
- Rascunho factual de privacidade em `PRIVACIDADE-EXTENSAO-RASCUNHO.md`.

## Justificativas de permissões atuais

| Permissão | Uso |
|---|---|
| contextMenus | Ação Baixar com OpenDownloader no clique direito |
| nativeMessaging | Comunicação local com app.lume.downloads |
| storage | Preferências locais, diagnóstico e candidatos de mídia da sessão |
| activeTab | Busca solicitada na página ativa |
| scripting | Inserção local da procura/botões de mídia |
| host permissions opcionais HTTP/HTTPS | Ativação por site e acesso aos frames autorizados |

Não foram acrescentadas permissões de cookies, proxy, downloads ou management.
A extensão não instala nem inicia código remoto; o aplicativo local recebe URLs
e faz as transferências por seus próprios componentes. A avaliação da loja precisa
considerar essa separação e o tratamento real das URLs/títulos.

## Pendências antes de publicar

1. Definir conta/identidade do editor e contato de suporte controlados pelo usuário.
2. Preparar ícones, screenshots e URL pública da política de privacidade aprovada.
3. A autorização para publicar foi seguida de pedido de suspensão. Retomar a
   criação de contas/envio somente quando o usuário retomar essa etapa.
4. Confirmar os IDs realmente atribuídos e atualizar a allowlist do host para os
   IDs de cada canal. O ID local `meicfjedeegbopmeifconaiekbmppbai` não é garantia
   do ID que será atribuído por uma loja. O campo key atual é chave pública.
5. Configurar URLs de loja e IDs correspondentes em browser-distribution.json;
   implementar/validar o anúncio externo de instalação apenas com itens publicados.
   Os botões existentes ficam desativados até haver metadados válidos.
6. Validar instalações limpas e atualizações em Chrome e Edge reais, com OpenDownloader
   inicialmente fechado, confirmação e conferência dos arquivos. Sem políticas
   de instalação forçada nem alteração de perfis do navegador.

Fontes oficiais consultadas:

- https://www.internetdownloadmanager.com/register/new_faq/chrome_extension.html
- https://www.internetdownloadmanager.com/register/new_faq/edge_extension.html
- https://developer.chrome.com/docs/extensions/how-to/distribute/install-extensions
- https://developer.chrome.com/docs/webstore/publish
- https://learn.microsoft.com/en-us/microsoft-edge/extensions/publish/publish-extension
