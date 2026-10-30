param(
    [Parameter(Mandatory = $true)][string]$SourceDirectory,
    [Parameter(Mandatory = $true)][string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$sourceRoot = (Resolve-Path -LiteralPath $SourceDirectory).Path
$outputRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
if ($sourceRoot -eq $outputRoot) { throw 'Private inputs and public outputs must be separate.' }
[System.IO.Directory]::CreateDirectory($outputRoot) | Out-Null

# Pixel rectangles were reviewed against the original captures. Do not use blur:
# these opaque pixels must replace the underlying text even in full-size files.
# Platform labels and real controls remain untouched. No original is embedded.
$regions = [ordered]@{
    'projects' = @(@(719,20,31,34), @(811,259,24,25), @(57,332,43,44), @(109,332,209,54), @(57,493,43,44), @(109,493,209,55))
    'create' = @(@(122,198,150,31), @(122,318,608,69), @(110,425,105,20))
    'create-audience' = @(@(122,314,216,31), @(122,407,278,31))
    'create-functions' = @()
    'create-review' = @(@(110,203,260,33), @(110,268,624,42), @(158,382,298,27), @(654,479,77,25))
    'builder' = @(@(140,10,134,26), @(80,218,750,29), @(112,267,119,24), @(640,310,129,23))
    'policies' = @()
    'catalog' = @(@(192,22,151,25), @(28,361,18,25), @(42,606,74,25), @(452,606,74,25), @(42,635,141,30), @(451,635,181,30))
    'catalog-edit' = @(@(49,150,164,31), @(606,309,62,31), @(606,341,150,25), @(606,367,189,38), @(606,414,78,26), @(49,437,192,29), @(329,437,130,29))
    'catalog-photo' = @(@(190,17,151,32), @(308,155,111,29), @(28,485,117,50), @(28,535,305,32), @(451,535,281,32), @(38,574,111,34), @(448,574,111,34))
    'integrations' = @(@(42,36,153,24), @(138,0,202,17))
    'max-bots' = @(@(50,62,270,28), @(98,175,65,65), @(99,250,223,34), @(99,286,223,28), @(99,316,610,31))
    'max-connection' = @(@(153,7,215,49), @(287,66,165,23), @(0,138,260,540), @(1020,138,260,540), @(260,677,760,43))
    'publish' = @(@(82,37,155,25), @(89,335,621,29), @(0,0,55,668), @(815,0,55,668), @(55,0,760,16), @(55,653,760,15))
    'max-address' = @()
    'max-settings' = @(@(43,42,66,68), @(43,116,217,50), @(359,268,307,34), @(886,77,99,95), @(866,171,159,28), @(866,199,162,24), @(805,316,236,40), @(207,652,60,16))
    'management' = ,@(192,22,151,25)
    'billing' = @(@(442,374,568,16), @(40,397,49,40), @(700,397,24,28), @(989,397,24,28), @(700,431,24,28), @(989,431,24,28), @(39,523,26,29), @(529,523,26,29))
    'support' = ,@(19,83,202,24)
}

$mask = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(255, 203, 213, 225))
try {
    foreach ($entry in $regions.GetEnumerator()) {
        $source = Join-Path $sourceRoot ($entry.Key + '.jpg')
        $destination = Join-Path $outputRoot ($entry.Key + '.png')
        $capture = [System.Drawing.Image]::FromFile($source)
        $bitmap = [System.Drawing.Bitmap]::new($capture.Width, $capture.Height, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        try {
            $graphics.DrawImageUnscaled($capture, 0, 0)
            foreach ($box in $entry.Value) {
                $x, $y, $width, $height = $box
                if ($x -lt 0 -or $y -lt 0 -or $width -le 0 -or $height -le 0 -or $x + $width -gt $bitmap.Width -or $y + $height -gt $bitmap.Height) {
                    throw "Redaction outside capture: $($entry.Key)"
                }
                $graphics.FillRectangle($mask, [int]$x, [int]$y, [int]$width, [int]$height)
            }
            $bitmap.Save($destination, [System.Drawing.Imaging.ImageFormat]::Png)
            [pscustomobject]@{ id = $entry.Key; width = $bitmap.Width; height = $bitmap.Height; masks = @($entry.Value).Count }
        } finally {
            $graphics.Dispose()
            $bitmap.Dispose()
            $capture.Dispose()
        }
    }
} finally {
    $mask.Dispose()
}
