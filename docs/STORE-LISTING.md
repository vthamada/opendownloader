# OpenDownloader — preparação de publicação da extensão 0.5.0

Estado: pacote local preparado; **não enviado, não publicado e sem aprovação das lojas**. A instalação externa automática ainda depende de publicação, IDs confirmados e integração correspondente no instalador. Não há garantia de aprovação. Requisitos consultados em 21/10/2026.

## Campos propostos para Chrome Web Store e Microsoft Edge Add-ons

**Nome:** OpenDownloader

**Idioma inicial:** português (Brasil).

**Descrição curta:** Envie arquivos e vídeos ao OpenDownloader ou grave a reprodução com escolha explícita da fonte.

**Propósito único:** guardar arquivos e mídia escolhidos pelo usuário no aplicativo OpenDownloader instalado no mesmo computador, por envio de links ou gravação explícita da reprodução.

**Descrição detalhada sugerida:**

> Envie seus próximos downloads ao OpenDownloader sem copiar e colar cada endereço. Use “Baixar com OpenDownloader” no clique direito de um link, vídeo ou áudio, ou cole um endereço no botão da extensão. O aplicativo instalado no computador apresenta a confirmação com nome, pasta e opções disponíveis antes da transferência, conforme suas preferências.
>
> “Procurar nesta página” identifica fontes de mídia expostas pela página autorizada. Quando disponíveis, os players recebem botões de envio. Você também pode permitir botões automáticos apenas nos sites que escolher e revogar essa permissão depois. Fontes alternativas detectadas no mesmo frame podem incluir anúncios ou segmentos: confira a fonte antes de enviar.
>
> “Gravar reprodução” abre uma aba própria. Escolha a fonte no seletor do navegador e compartilhe áudio se desejar. Pause ou encerre a gravação; o aplicativo recebe o arquivo localmente e oferece nome e pasta ao terminar. Conteúdo protegido pode ser bloqueado pelo navegador. O microfone não é solicitado.
>
> É necessário instalar o aplicativo OpenDownloader para Windows de 64 bits. A extensão sozinha não baixa arquivos. O suporte depende das fontes oferecidas pelo site e dos formatos reconhecidos pelo aplicativo. Nem todo player ou vídeo pode ser baixado. Não há suporte universal, remoção de DRM ou importação de cookies e sessões de login. Use somente conteúdo que você tenha autorização para baixar.

Não acrescentar promessa de aceleração medida, funcionamento em todos os sites, homologação nas lojas ou instalação automática da extensão antes das respectivas provas.

**Categoria sugerida:** produtividade, se disponível no painel. Confirmar a categoria real antes do envio.

**Palavras de busca opcionais:** downloads, arquivos, vídeos, áudio, gerenciador de downloads.

## Justificativas de permissões

| Permissão | Uso concreto no código |
| --- | --- |
| `contextMenus` | Oferece “Baixar com OpenDownloader” em links e fontes de vídeo/áudio escolhidos pelo usuário. |
| `nativeMessaging` | Envia pedidos e blocos da gravação explicitamente selecionada ao host local `app.lume.downloads`; também consulta seu estado no diagnóstico. O host precisa ser instalado pelo aplicativo. |
| `storage` | Mantém preferências locais dos sites autorizados e o último resultado de envio; usa armazenamento de sessão para candidatos, identidade dos documentos e concessões temporárias. |
| `activeTab` | Permite procurar mídias na aba atual após uma ação explícita do usuário. Não concede acesso permanente a todos os sites. |
| `scripting` | Injeta os scripts de descoberta e os botões nos documentos autorizados; remove-os quando o acesso é revogado. |
| `optional_host_permissions`: `http://*/*`, `https://*/*` | Permite solicitar acesso ao endereço escolhido para botões automáticos. Nenhum site recebe essa autorização automaticamente na instalação. A amplitude dos padrões permite que o usuário escolha sites diferentes, sem uma lista fixa de domínios. |

Não foram adicionadas permissões nesta preparação. Não há acesso a cookies, proxy, gerenciamento de outras extensões ou API de histórico. Não declarar “nenhum dado acessado”: a descoberta examina URLs de mídia e títulos nas páginas autorizadas.

## Código remoto e dados

**Uso de código remoto na extensão: não.** JavaScript, CSS, HTML e ícones executados pela extensão estão no ZIP. O `importScripts` usa `media-core.js` local. A descoberta consulta o DOM/Resource Timing autorizado; não baixa scripts de servidor para executá-los. O aplicativo complementar é distribuído separadamente e deve ser identificado ao revisor.

**Descrição factual dos dados para o formulário de privacidade:** URLs e títulos das páginas/fontes autorizadas são processados localmente para exibir candidatos. O candidato escolhido é enviado ao aplicativo local por Native Messaging. A sessão do navegador mantém listas e concessões limitadas; preferências de sites e o último resultado são persistidos localmente. O aplicativo conserva pedidos/fila e acessa o servidor do arquivo para realizar o download, sujeito à sua própria política. A extensão não possui endpoint de telemetria nem servidor do editor para receber esses dados. URLs fornecidas pelo usuário podem conter parâmetros sensíveis; não prometer que jamais serão processados. Não importar credenciais, cookies ou sessões de login.

O responsável deve selecionar e conferir as categorias de dados no formulário real de cada loja com base nesses fatos e na política pública final. Não marcar declarações legais em nome do responsável. “Processamento local” não dispensa a divulgação dos acessos/transmissão ao aplicativo complementar.

## Assets preparados e screenshots pendentes

`tools/create_extension_icons.py` gera arte original: quadrado arredondado verde, contorno claro e seta para baixo. Não utiliza arte externa ou fontes. Os PNG de 16, 32, 48 e 128 pixels ficam em `browser-extension/icons`; o SVG original acompanha o código-fonte, mas não entra no ZIP mínimo porque não é usado em execução. O ícone de 128 pixels inclui margem transparente de 16 pixels em torno da forma quadrada de 96 pixels.

Arte adicional em `.cache/store-assets`: `logo-300.png` e `promo-440x280.png`. A peça promocional é arte de marca, **não uma captura de tela**. [Requisitos de imagens do Chrome](https://developer.chrome.com/docs/webstore/images): ícone PNG de 128 pixels, peça promocional de 440 × 280 e pelo menos uma captura real de 1280 × 800 ou 640 × 400. A [documentação do Edge](https://learn.microsoft.com/en-us/microsoft-edge/extensions/publish/publish-extension) recomenda logo de 300 × 300 (mínimo 128), aceita peça de 440 × 280 e trata capturas como opcionais.

Capturas reais ainda precisam mostrar, sem dados privados:

1. O popup da extensão no navegador com o nome OpenDownloader e as ações de envio/procura.
2. Um player público/de teste com o botão de envio e a confirmação no aplicativo, sem sugerir que todo player funciona.

Não fabricar screenshot, reutilizar a interface de outro programa ou apresentar uma montagem como prova do navegador real. Chrome/Edge devem ser recarregados com a versão preparada antes da captura.

## Instruções propostas ao revisor

**Pré-requisito a preencher antes da submissão:** `[URL_PUBLICA_REAL_DO_INSTALADOR]`, versão do aplicativo e SHA-256 correspondente. O instalador precisa estar acessível ao revisor e conter a integração com o ID efetivamente atribuído à extensão na loja. Não enviar apenas um caminho local ou URL fictícia. Os IDs finais não estão confirmados; a chave atual conserva somente a identidade local já utilizada.

1. Em Windows de 64 bits, instalar o aplicativo complementar pelo endereço público real fornecido. Não são necessárias credenciais de teste para o fluxo básico.
2. Instalar a extensão submetida e autorizar a integração. No popup, usar “Verificar conexão com o aplicativo”. Deve aparecer uma resposta da ponte; esse diagnóstico não inicia download.
3. Usar um link HTTP(S) público de arquivo de teste sem autenticação, ou servir um arquivo próprio com `python -m http.server 8765 --bind 127.0.0.1` e enviar `http://127.0.0.1:8765/arquivo-de-teste.bin` pelo campo do popup. Esse servidor é apenas fixture de revisão, não requisito do produto.
4. Com o aplicativo fechado, enviar o link. A confirmação deve abrir automaticamente. Conferir nome/pasta, selecionar “Baixar agora” e verificar o conteúdo final contra a origem. Repetir com “Só adicionar à fila” e iniciar depois pelo aplicativo. O recebimento do pedido na ponte não é conclusão do download.
5. Em uma página de teste contendo `<video src="URL_HTTP_DE_VIDEO_MP4_PROPRIO">`, usar “Procurar nesta página”. O botão deve enviar o candidato ao aplicativo para a confirmação. A descoberta não inicia transferência sozinha.
6. Se testar botões automáticos, autorizá-los apenas no site de teste e depois desativá-los. Confirmar a remoção dos botões e da preferência/permissão para aquele site. Repetir os fluxos em Chrome e Edge.
7. Sem o aplicativo/host registrado, o popup deve exibir o erro da ponte e não declarar download concluído. Não é solicitado acesso a cookies nem credenciais.

Os nomes das opções seguem os controles atuais. Esses passos são instruções propostas, não declaração de execução da versão 0.3.4 em navegadores reais ou em máquina limpa.

## Dados do responsável e bloqueios antes de submeter

| Campo | Situação |
| --- | --- |
| Nome legal/editor responsável e conta Chrome | Pendente de informação do usuário. |
| Nome legal/editor responsável e conta Partner Center | Pendente de informação do usuário. |
| E-mail público de suporte | Pendente; não inventar. |
| Site/suporte público | Pendente, se adotado/exigido pelo painel. |
| Política de privacidade pública | `[URL_PUBLICA_REAL_DA_POLITICA]`; publicar texto final com responsável e contato reais antes do envio. |
| Instalador acessível ao revisor/usuário | `[URL_PUBLICA_REAL_DO_INSTALADOR]`; entrega atual é local. |
| Visibilidade | Proposta: não listada/oculta; o usuário precisa confirmar no painel, não foi aplicada. |
| IDs Chrome e Edge | Desconhecidos até criação dos itens; devem entrar na allowlist do host e no anúncio do instalador. |
| Captura real Chrome | Pendente. |
| Captura real Edge | Pendente; recomendada para revisão e validação, mesmo quando opcional na listagem. |

Não abrir conta, pagar taxa, enviar ZIP, aceitar termos ou publicar por este procedimento de preparação. A aprovação ou criação de item não autoriza instalação forçada, políticas corporativas ou alteração direta dos perfis dos navegadores.

## Reproduzir o pacote local

Na pasta `local-download-manager`:

```powershell
.\.venv\Scripts\python.exe tools\create_extension_icons.py
.\.venv\Scripts\python.exe tools\package_extension.py
```

O segundo comando produz `.cache/store-assets/OpenDownloaderExtension-0.3.4.zip`, arquivo `.sha256` e `.inventory.json` com os hashes de cada recurso. O ZIP usa ordenação e metadados fixos e armazenamento sem compressão, exclui README/testes/arte não utilizada e mantém o manifesto na raiz. Falta de recurso local referenciado, mudança de permissões ou alteração da chave local interrompem a geração. Uma saída existente com conteúdo diferente não é sobrescrita.

Esse cheque valida as referências literais/estruturas atuais de empacotamento; não é um analisador completo de JavaScript nem substitui testes de runtime ou revisão da loja. Instruções oficiais de envio: [Chrome Web Store](https://developer.chrome.com/docs/webstore/publish) e [Microsoft Edge Add-ons](https://learn.microsoft.com/en-us/microsoft-edge/extensions/publish/publish-extension).
