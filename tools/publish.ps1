# ═══════════════════════════════════════════════════════════════════════
#  أداة نشر موقع التطبيقات — تعمل على حاسبتك فقط.
#  لا لوحة إدارة على الإنترنت: النشر يحتاج حاسبتك + حساب GitHub بتحقق بخطوتين.
#
#  التشغيل: زر أيمن على «نشر.cmd» ← فتح، أو:
#     powershell -ExecutionPolicy Bypass -File tools\publish.ps1
#  تحتاج: Git و GitHub CLI (gh). التثبيت مرة واحدة:
#     winget install --id Git.Git -e ; winget install --id GitHub.cli -e
# ═══════════════════════════════════════════════════════════════════════
#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$Platforms = [ordered]@{
  'windows' = @{ Label = 'ويندوز'; Exts = @('.exe', '.msix', '.msi', '.zip') }
  'android' = @{ Label = 'أندرويد'; Exts = @('.apk') }
}

function Say([string]$m, [string]$c = 'Gray') { Write-Host $m -ForegroundColor $c }
function Ok([string]$m) { Say "✓ $m" 'Green' }
function Warn([string]$m) { Say "! $m" 'Yellow' }
function Die([string]$m) { throw $m }  # تُطبع الرسالة مرة واحدة في القائمة

function Write-Utf8([string]$path, [string]$text) {
  [IO.File]::WriteAllText($path, $text, $Utf8NoBom)
}

function Ask([string]$q, [string]$pattern = '.+', [string]$hint = '') {
  while ($true) {
    $a = (Read-Host $q).Trim()
    if ($a -match "^(?:$pattern)$") { return $a }
    if ($hint) { Warn $hint } else { Warn 'قيمة غير صالحة، أعد المحاولة.' }
  }
}

function Confirm([string]$q) {
  $a = (Read-Host "$q (نعم/لا)").Trim()
  return ($a -eq 'نعم' -or $a -eq 'ن' -or $a -eq 'y' -or $a -eq 'yes')
}

function Need([string]$exe, [string]$install) {
  if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {
    Die "البرنامج «$exe» غير مثبّت. ثبّته بالأمر:`n    $install`nثم أغلق هذه النافذة وأعد فتحها."
  }
}

function Run([string]$exe, [string[]]$argv) {
  & $exe @argv
  if ($LASTEXITCODE -ne 0) { Die "فشل الأمر: $exe $($argv -join ' ')" }
}

function Get-Repo {
  $site = Get-Content (Join-Path $Root 'site.json') -Raw -Encoding UTF8 | ConvertFrom-Json
  $repo = [string]$site.repo
  if ($repo -match '^OWNER/' -or -not $repo) {
    $repo = (& gh repo view --json nameWithOwner -q .nameWithOwner 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $repo) { Die 'لم يُعرف مستودع GitHub. نفّذ «إعداد لأول مرة» من القائمة.' }
    $site.repo = $repo
    Write-Utf8 (Join-Path $Root 'site.json') ($site | ConvertTo-Json -Depth 6)
    Ok "المستودع: $repo"
  }
  return $repo
}

# يمنع رفع أي سر أو ملف تطبيق إلى المستودع حتى لو أُضيف خطأً
function Assert-CleanStage {
  $staged = @(& git diff --cached --name-only)
  $bad = $staged | Where-Object { $_ -match '\.(jks|keystore|pfx|p12|pem|key|exe|msi|msix|apk|aab|zip)$' -or $_ -match '(^|/)(key\.properties|\.env)' }
  if ($bad) {
    & git reset -q
    Die "أُوقف النشر: ملفات لا يجوز رفعها إلى المستودع:`n  $($bad -join "`n  ")"
  }
  $big = $staged | Where-Object { (Test-Path $_) -and ((Get-Item $_).Length -gt 15MB) }
  if ($big) {
    & git reset -q
    Die "أُوقف النشر: ملفات أكبر من 15 ميغابايت (الصور يجب أن تكون أصغر):`n  $($big -join "`n  ")"
  }
}

function Push-Changes([string]$message) {
  Run git @('add', '--', 'apps', 'site.json')
  Assert-CleanStage
  $pending = @(& git diff --cached --name-only)
  if ($pending.Count -eq 0) { Warn 'لا تغييرات جديدة للنشر.'; return }
  Run git @('commit', '-q', '-m', $message)
  Run git @('push', '-q', 'origin', 'HEAD:main')
  Ok 'رُفعت التغييرات. GitHub يبني الموقع الآن (دقيقة إلى دقيقتين).'
  Say "  تابع البناء:  gh run watch   أو صفحة Actions في المستودع."
}

function Select-App {
  $apps = @(Get-ChildItem (Join-Path $Root 'apps') -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'app.json') })
  if ($apps.Count -eq 0) { Die 'لا تطبيقات بعد. أضف تطبيقاً من القائمة أولاً.' }
  Say ''
  for ($i = 0; $i -lt $apps.Count; $i++) {
    $j = Get-Content (Join-Path $apps[$i].FullName 'app.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    Say ("  {0}) {1}  [{2}]" -f ($i + 1), $j.name, $apps[$i].Name)
  }
  $n = [int](Ask 'رقم التطبيق' '\d{1,3}')
  if ($n -lt 1 -or $n -gt $apps.Count) { Die 'رقم غير موجود.' }
  return $apps[$n - 1]
}

# APK موقّع بمفتاح debug = لا يُنشر: لا يقبل الهاتف تحديثه لاحقاً بمفتاحك الحقيقي إلا بحذف التطبيق وبياناته
function Assert-ApkReleaseSigned([string]$apk) {
  $cands = @()
  if ($env:JAVA_HOME) { $cands += (Join-Path $env:JAVA_HOME 'bin\keytool.exe') }
  $cands += 'C:\Program Files\Android\Android Studio\jbr\bin\keytool.exe'
  $cands += 'C:\Program Files\Android\Android Studio\jre\bin\keytool.exe'
  $kt = $cands | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
  if (-not $kt) { $c = Get-Command keytool -ErrorAction SilentlyContinue; if ($c) { $kt = $c.Source } }
  if (-not $kt) {
    Warn 'لم أجد keytool لفحص توقيع الـAPK. تأكد بنفسك أنه ليس موقّعاً بمفتاح debug.'
    if (-not (Confirm 'متأكد أن الملف موقّع بمفتاحك الحقيقي؟')) { Die 'أُلغي.' }
    return
  }
  $out = (& $kt -printcert -jarfile $apk 2>&1) -join "`n"
  if ($out -match 'CN=Android Debug') {
    Die 'هذا الـAPK موقّع بمفتاح debug. أنشئ مفتاح النشر وملف android\key.properties ثم أعد البناء (flutter build apk --release).'
  }
  if ($out -notmatch 'SHA256') { Warn "تعذّر قراءة توقيع الـAPK:`n$out"; if (-not (Confirm 'أكمل رغم ذلك؟')) { Die 'أُلغي.' } ; return }
  $fp = ([regex]::Match($out, 'SHA256:\s*([0-9A-F:]+)')).Groups[1].Value
  Ok "الـAPK موقّع بمفتاح نشر. بصمة الشهادة: $fp"
}

function Pick-File([string]$platform) {
  try {
    Add-Type -AssemblyName System.Windows.Forms
    $d = New-Object System.Windows.Forms.OpenFileDialog
    $d.Title = "اختر ملف التطبيق ($($Platforms[$platform].Label))"
    if ($platform -eq 'android') { $d.Filter = 'APK (*.apk)|*.apk' }
    else { $d.Filter = 'برنامج ويندوز (*.exe;*.msix;*.msi;*.zip)|*.exe;*.msix;*.msi;*.zip' }
    $guess = Join-Path $env:USERPROFILE 'Desktop\alnasser-accounting-edition'
    $sub = if ($platform -eq 'android') { 'build\app\outputs\flutter-apk' } else { 'installer_output' }
    if (Test-Path (Join-Path $guess $sub)) { $d.InitialDirectory = (Join-Path $guess $sub) }
    $top = New-Object System.Windows.Forms.Form -Property @{ TopMost = $true }
    if ($d.ShowDialog($top) -eq [System.Windows.Forms.DialogResult]::OK) { return $d.FileName }
  } catch { Warn "تعذّر فتح نافذة الاختيار: $($_.Exception.Message)" }
  return $null
}

# ═════════════════════ 1) نشر إصدار ═════════════════════
function Publish-Release {
  $repo = Get-Repo
  $app = Select-App
  $appId = $app.Name
  $meta = Get-Content (Join-Path $app.FullName 'app.json') -Raw -Encoding UTF8 | ConvertFrom-Json

  Say "`n  1) ويندوز   2) أندرويد"
  $p = Ask 'المنصة' '[12]'
  $platform = @('windows', 'android')[[int]$p - 1]
  $label = $Platforms[$platform].Label

  $version = Ask 'رقم الإصدار (مثل 1.0.0)' '\d+\.\d+\.\d+(\.\d+)?([-+][0-9A-Za-z.]{1,20})?' 'اكتب الإصدار بثلاثة أرقام مثل 1.0.0'
  $relDir = Join-Path $app.FullName 'releases'
  $relFile = Join-Path $relDir "$platform-$version.json"
  if (Test-Path $relFile) { Die "الإصدار $version لـ$label منشور سابقاً. ارفع الرقم (مثلاً $version.1)." }

  # نافذة اختيار ملف عادية (بدل السحب أو كتابة المسار)
  Say 'ستُفتح نافذة لاختيار ملف التطبيق…' 'Cyan'
  $path = Pick-File $platform
  if (-not $path) { $path = (Read-Host 'لم تختر ملفاً. اكتب مسار الملف أو اتركه فارغاً للإلغاء').Trim().Trim('"').Trim("'") }
  if (-not $path) { Warn 'أُلغي.'; return }
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { Die "الملف غير موجود: $path" }
  $src = Get-Item -LiteralPath $path
  $ext = $src.Extension.ToLowerInvariant()
  if ($Platforms[$platform].Exts -notcontains $ext) { Die "امتداد $ext لا يناسب $label (المسموح: $($Platforms[$platform].Exts -join ' '))" }
  if ($src.Length -ge 2GB) { Die 'GitHub لا يقبل ملفاً أكبر من 2 غيغابايت.' }
  if ($platform -eq 'android') { Assert-ApkReleaseSigned $src.FullName }

  Say 'ما الجديد في هذا الإصدار؟ سطر لكل نقطة، وسطر فارغ للإنهاء:'
  $notes = New-Object System.Collections.Generic.List[string]
  while ($true) { $l = (Read-Host ' •').Trim(); if (-not $l) { break }; $notes.Add($l) }

  $assetName = "$appId-$version-$platform$ext"
  $tag = "$appId-$platform-v$version"
  $tmpDir = Join-Path $env:TEMP ("pub-" + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $tmpDir | Out-Null
  try {
    $asset = Join-Path $tmpDir $assetName
    Copy-Item -LiteralPath $src.FullName -Destination $asset
    $sha = (Get-FileHash -LiteralPath $asset -Algorithm SHA256).Hash.ToLowerInvariant()
    $size = (Get-Item -LiteralPath $asset).Length

    Say "`n──────── تأكيد ────────" 'Cyan'
    Say "  التطبيق : $($meta.name)"
    Say "  المنصة  : $label"
    Say "  الإصدار : $version"
    Say "  الملف   : $assetName  ($([math]::Round($size / 1MB, 1)) MB)"
    Say "  البصمة  : $sha"
    if (-not (Confirm 'أنشر هذا الإصدار للعامة؟')) { Warn 'أُلغي.'; return }

    & gh release view $tag --repo $repo *> $null
    if ($LASTEXITCODE -eq 0) { Die "يوجد إصدار على GitHub بالوسم $tag. ارفع رقم الإصدار." }

    $notesPath = Join-Path $tmpDir 'notes.md'
    $body = "**$($meta.name) — $label — $version**`n`n"
    foreach ($n in $notes) { $body += "- $n`n" }
    $body += "`nSHA-256: ``$sha``"
    Write-Utf8 $notesPath $body

    Say 'رفع الملف إلى GitHub Releases…'
    Run gh @('release', 'create', $tag, $asset, '--repo', $repo, '--title', "$($meta.name) $version ($label)", '--notes-file', $notesPath)

    # تحقق بعد الرفع: الحجم، والبصمة إن أعادها GitHub
    $info = (& gh release view $tag --repo $repo --json assets) | ConvertFrom-Json
    $up = $info.assets | Where-Object { $_.name -eq $assetName } | Select-Object -First 1
    if (-not $up) { Die "لم يظهر الملف $assetName في الإصدار بعد الرفع. راجع صفحة الإصدارات." }
    if ([int64]$up.size -ne $size) { Die "حجم الملف على GitHub ($($up.size)) يختلف عن ملفك ($size). لا يُنشر الموقع." }
    if (($up.PSObject.Properties.Name -contains 'digest') -and $up.digest) {
      if ($up.digest -ne "sha256:$sha") { Die "بصمة GitHub ($($up.digest)) تختلف عن بصمة ملفك. لا يُنشر الموقع." }
      Ok 'GitHub أكّد البصمة نفسها.'
    }
    $url = "https://github.com/$repo/releases/download/$tag/$assetName"

    New-Item -ItemType Directory -Force -Path $relDir | Out-Null
    $rec = [ordered]@{
      platform = $platform
      version  = $version
      file     = $assetName
      size     = $size
      sha256   = $sha
      url      = $url
      date     = (Get-Date).ToString('yyyy-MM-dd')
      notes    = @($notes)
    }
    Write-Utf8 $relFile (ConvertTo-Json -InputObject $rec -Depth 4)
    Ok "سُجّل الإصدار في apps\$appId\releases\$platform-$version.json"
    Push-Changes "release: $appId $platform $version"
  }
  finally {
    Remove-Item -LiteralPath $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
  }
}

# ═════════════════════ 2) تطبيق جديد ═════════════════════
function New-App {
  $id = Ask 'معرّف التطبيق بالإنجليزية (حروف صغيرة وأرقام وشرطة، مثل stock-counter)' '[a-z0-9][a-z0-9-]{1,40}' 'حروف إنجليزية صغيرة وأرقام وشرطة فقط'
  $dir = Join-Path (Join-Path $Root 'apps') $id
  if (Test-Path $dir) { Die "التطبيق $id موجود." }
  $name = Ask 'اسم التطبيق كما يظهر للناس'
  $tagline = (Read-Host 'جملة واحدة تصف ما يفعله (اختياري)').Trim()
  New-Item -ItemType Directory -Path (Join-Path $dir 'screenshots'), (Join-Path $dir 'releases') -Force | Out-Null
  $meta = [ordered]@{
    name         = $name
    tagline      = $tagline
    order        = 50
    description  = @()
    features     = @()
    requirements = [ordered]@{ windows = 'ويندوز 10 أو 11 (64 بت)'; android = 'أندرويد 8 فما فوق' }
    screenshots  = [ordered]@{}
  }
  Write-Utf8 (Join-Path $dir 'app.json') (ConvertTo-Json -InputObject $meta -Depth 5)
  Ok "أُنشئ apps\$id"
  Say '  ضع لقطات الشاشة في مجلد screenshots (PNG أو JPG)، وعدّل الوصف والميزات في app.json،'
  Say '  ثم اختر «نشر الوصف واللقطات» أو «نشر إصدار».'
  Start-Process explorer.exe $dir
}

# ═════════════════════ 3) الوصف واللقطات فقط ═════════════════════
function Publish-Content {
  $null = Get-Repo
  Push-Changes 'content: update descriptions/screenshots'
}

# ═════════════════════ 4) إعداد لأول مرة ═════════════════════
function Setup-First {
  & gh auth status *> $null
  if ($LASTEXITCODE -ne 0) {
    Say 'سيُفتح المتصفح لتسجيل الدخول إلى GitHub. سجّل أنت بنفسك (لا تعطِ الرمز لأحد).' 'Cyan'
    Run gh @('auth', 'login', '--web', '--git-protocol', 'https')
  }
  Ok 'مسجّل في GitHub.'
  if (-not (Test-Path (Join-Path $Root '.git'))) {
    Run git @('init', '-q', '-b', 'main')
  }
  & git remote get-url origin *> $null
  if ($LASTEXITCODE -ne 0) {
    $name = Ask 'اسم المستودع على GitHub (مثل alnasser-apps)' '[A-Za-z0-9._-]{1,100}'
    Run git @('add', '-A')
    Assert-CleanStage
    Run git @('commit', '-q', '-m', 'initial site')
    Run gh @('repo', 'create', $name, '--public', '--source', '.', '--remote', 'origin', '--push',
      '--description', 'Official download site')
  }
  $repo = Get-Repo
  Push-Changes 'config: repo'
  Say 'تفعيل GitHub Pages بالنشر من Actions…'
  & gh api -X POST "repos/$repo/pages" -f build_type=workflow *> $null
  & gh api -X PUT "repos/$repo/pages" -f build_type=workflow -F https_enforced=true *> $null
  Ok "الموقع سيكون على: https://$($repo.Split('/')[0].ToLower()).github.io/$($repo.Split('/')[1])/"
  Say ''
  Say 'بقيت خطوات تأمين تفعلها أنت مرة واحدة (مذكورة في «الأمان.md»):' 'Cyan'
  Say '  1) فعّل التحقق بخطوتين على حساب GitHub (الأهم على الإطلاق).'
  Say '  2) لا تعطِ أحداً صلاحية على المستودع.'
}

# ═════════════════════ 5) معاينة محلية ═════════════════════
function Preview {
  $py = Get-Command python -ErrorAction SilentlyContinue
  if (-not $py) { Die 'المعاينة تحتاج Python: winget install --id Python.Python.3.12 -e  ثم: python -m pip install pillow' }
  Run python @('tools/build.py')
  Say 'المعاينة على http://127.0.0.1:8000  (أغلق النافذة لإيقافها)' 'Cyan'
  Start-Process 'http://127.0.0.1:8000/'
  & python -m http.server 8000 --bind 127.0.0.1 -d dist
}

# ═════════════════════ القائمة ═════════════════════
try {
  Need git 'winget install --id Git.Git -e'
  Need gh 'winget install --id GitHub.cli -e'
}
catch { Say "✗ $($_.Exception.Message)" 'Red'; return }

while ($true) {
  Say "`n═══ نشر موقع التطبيقات ═══" 'Cyan'
  Say '  1) نشر إصدار جديد (ملف exe أو apk)'
  Say '  2) إضافة تطبيق جديد'
  Say '  3) نشر تعديل الوصف واللقطات فقط'
  Say '  4) إعداد لأول مرة'
  Say '  5) معاينة الموقع على حاسبتي'
  Say '  0) خروج'
  $c = (Read-Host 'اختر').Trim()
  try {
    switch ($c) {
      '1' { Publish-Release }
      '2' { New-App }
      '3' { Publish-Content }
      '4' { Setup-First }
      '5' { Preview }
      '0' { return }
      default { Warn 'اختيار غير موجود.' }
    }
  }
  catch {
    if ($_.Exception.Message) { Say "✗ $($_.Exception.Message)" 'Red' }
  }
}
