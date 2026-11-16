; BedrockForge portable (ADR 0005 §3): one .exe without installation and without admin rights.
; First run unpacks the app once next to the .exe (or to %LOCALAPPDATA%\BedrockForge\portable\<version>
; when that folder is read-only); later runs start the unpacked app at once.
; Build: makensis /DVERSION=x.y.z /DSRC=<win-unpacked> /DOUTFILE=<...-portable.exe> tools/portable.nsi
; Parameters: /extract-only — unpack and exit (CI timing).

Unicode true
!include "FileFunc.nsh"
!include "LogicLib.nsh"

!ifndef VERSION
  !error "VERSION is required"
!endif
!ifndef SRC
  !error "SRC (win-unpacked) is required"
!endif
!ifndef OUTFILE
  !define OUTFILE "BedrockForge-${VERSION}-portable.exe"
!endif

Name "BedrockForge ${VERSION}"
Caption "BedrockForge by Mikhail Bandurkin"
OutFile "${OUTFILE}"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64
ShowInstDetails nevershow
AutoCloseWindow true
BrandingText "BedrockForge by Mikhail Bandurkin"
!if /FileExists "${NSISDIR}\Contrib\Language files\Russian.nlf"
  LoadLanguageFile "${NSISDIR}\Contrib\Language files\Russian.nlf"
!endif
SubCaption 3 " — подготовка к первому запуску…"
Page instfiles

Var Target
Var Params
Var ExtractOnly

Function Ready
  ; $0 = folder; returns 1 in $1 when the app of this version is already unpacked there
  ${If} ${FileExists} "$0\.bf-ready-${VERSION}"
  ${AndIf} ${FileExists} "$0\BedrockForge.exe"
    StrCpy $1 1
  ${Else}
    StrCpy $1 0
  ${EndIf}
FunctionEnd

Function .onInit
  ${GetParameters} $Params
  StrCpy $ExtractOnly 0
  ${GetOptions} $Params "/extract-only" $R0
  ${IfNot} ${Errors}
    StrCpy $ExtractOnly 1
  ${EndIf}

  StrCpy $Target "$EXEDIR\BedrockForge-${VERSION}"
  StrCpy $0 $Target
  Call Ready
  ${If} $1 == 0
    StrCpy $0 "$LOCALAPPDATA\BedrockForge\portable\${VERSION}"
    Call Ready
    ${If} $1 == 1
      StrCpy $Target $0
    ${EndIf}
  ${EndIf}
  ${If} $1 == 1
  ${AndIf} $ExtractOnly == 0
    Exec '"$Target\BedrockForge.exe" $Params'
    Quit
  ${EndIf}

  ; Not unpacked yet: is the folder next to the .exe writable?
  ClearErrors
  CreateDirectory "$Target"
  FileOpen $2 "$Target\.bf-write-test" w
  ${If} ${Errors}
    StrCpy $Target "$LOCALAPPDATA\BedrockForge\portable\${VERSION}"
  ${Else}
    FileClose $2
    Delete "$Target\.bf-write-test"
  ${EndIf}
FunctionEnd

Section
  ; Two first runs at once must not unpack into the same folder.
  System::Call 'kernel32::CreateMutexW(p 0, i 0, w "BedrockForgePortable-${VERSION}") p .r3 ?e'
  Pop $4
  ${If} $4 == 183
    MessageBox MB_OK|MB_ICONINFORMATION "BedrockForge уже готовится к первому запуску. Дождитесь окончания."
    Quit
  ${EndIf}
  ClearErrors
  SetOutPath "$Target"
  File /r "${SRC}\*.*"
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONSTOP "Не удалось распаковать BedrockForge в $Target. Освободите место на диске или запустите файл из другой папки."
    Quit
  ${EndIf}
  FileOpen $2 "$Target\.bf-ready-${VERSION}" w
  FileWrite $2 "${VERSION}"
  FileClose $2
  ${If} $ExtractOnly == 0
    Exec '"$Target\BedrockForge.exe" $Params'
  ${EndIf}
SectionEnd
