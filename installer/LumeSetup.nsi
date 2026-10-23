; Build with the pinned portable NSIS compiler. No browser policies/profile edits.
Unicode true
RequestExecutionLevel user
SetCompressor /SOLID zlib
!include MUI2.nsh
!include LogicLib.nsh
!include x64.nsh

!ifndef PAYLOAD
  !error "PAYLOAD must name the verified, assembled OpenDownloader directory"
!endif
!ifndef OUTPUT
  !error "OUTPUT must name the resulting installer"
!endif
!ifndef APP_VERSION
  !error "APP_VERSION is required"
!endif
!ifndef MAINTENANCE
  !error "MAINTENANCE must name the independently packaged maintenance helper"
!endif

Name "OpenDownloader ${APP_VERSION}"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\LumeDownloads"
BrandingText "OpenDownloader"
VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey /LANG=1046 "ProductName" "OpenDownloader"
VIAddVersionKey /LANG=1046 "FileDescription" "Instalador do OpenDownloader"
VIAddVersionKey /LANG=1046 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=1046 "LegalCopyright" "OpenDownloader"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Instale o OpenDownloader"
!define MUI_WELCOMEPAGE_TEXT "Instale o aplicativo e os componentes de vídeo em uma única etapa. O assistente cria os atalhos e prepara a conexão com Chrome e Edge.$\r$\n$\r$\nNo final, você poderá configurar a extensão com instruções para cada navegador. Na instalação local, é necessário carregá-la uma vez.$\r$\n$\r$\nSua fila e seus downloads são preservados."
!insertmacro MUI_PAGE_WELCOME
!define MUI_DIRECTORYPAGE_TEXT_TOP "Escolha a pasta permanente do OpenDownloader. O aplicativo e a ponte serão configurados para o seu usuário, sem alterar o PATH ou instalar serviços. Feche o OpenDownloader antes de atualizar."
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "OpenDownloader instalado"
!define MUI_FINISHPAGE_TEXT "O OpenDownloader está pronto para abrir pelo atalho da Área de Trabalho ou do menu Iniciar.$\r$\n$\r$\nO próximo assistente mostra os passos para Chrome e Edge, copia o caminho da extensão e ajuda a abrir o gerenciador de cada navegador.$\r$\n$\r$\nConclua a configuração uma vez em cada navegador."
!define MUI_FINISHPAGE_RUN "$INSTDIR\app\LumeDownloads.exe"
!define MUI_FINISHPAGE_RUN_PARAMETERS '--integration-center --installation-root $\"$INSTDIR\.$\"'
!define MUI_FINISHPAGE_RUN_TEXT "Configurar a extensão no Chrome e no Edge agora"
!insertmacro MUI_PAGE_FINISH
!define MUI_UNCONFIRMPAGE_TEXT_TOP "O OpenDownloader removerá somente os arquivos e registros próprios. Sua fila e seus downloads serão preservados. Arquivos desconhecidos ou modificados permanecem para revisão. Feche o aplicativo antes de continuar."
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "PortugueseBR"

Function .onInit
  SetShellVarContext current
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "Este pacote do OpenDownloader exige Windows de 64 bits." /SD IDOK
    Abort
  ${EndIf}
FunctionEnd

Section "OpenDownloader"
  InitPluginsDir
  SetOutPath "$PLUGINSDIR\payload"
  File /r "${PAYLOAD}\*"
  SetOutPath "$PLUGINSDIR"
  File /oname=LumeMaintenance.exe "${MAINTENANCE}"
  WriteUninstaller "$PLUGINSDIR\Uninstall.exe"
  DetailPrint "Conferindo arquivos, preparando backup e configurando a ponte local…"
  nsExec::ExecToStack '"$PLUGINSDIR\LumeMaintenance.exe" --install-local --source-directory "$PLUGINSDIR\payload\." --destination "$INSTDIR\." --uninstaller "$PLUGINSDIR\Uninstall.exe" --apply --report "$PLUGINSDIR\installation-result.json"'
  Pop $0
  Pop $1
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONSTOP "Não foi possível concluir a instalação.$\r$\n$\r$\n$1" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  DetailPrint "Aplicativo, atalhos e conexão local configurados."
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  InitPluginsDir
  DetailPrint "Usando manutenção independente do aplicativo instalado…"
  SetOutPath "$PLUGINSDIR"
  File /oname=LumeMaintenance.exe "${MAINTENANCE}"
  nsExec::ExecToStack '"$PLUGINSDIR\LumeMaintenance.exe" --uninstall-local --install-directory "$INSTDIR\." --apply --report "$PLUGINSDIR\uninstall-result.json"'
  Pop $0
  Pop $1
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONSTOP "A remoção precisa de revisão. Os dados do usuário foram preservados.$\r$\n$1" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  DetailPrint "Remoção finalizada. Fila, downloads e arquivos externos preservados."
SectionEnd
