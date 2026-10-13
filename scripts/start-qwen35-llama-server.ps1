param(
    [string]$LlamaServer = "$env:USERPROFILE\.lmstudio\extensions\backends\llama.cpp-win-x86_64-nvidia-cuda12-avx2-2.50.0\llama-server.exe",
    # GGUF 模型檔完整路徑，例如 -Model "D:\models\Qwen3.5-9B-Q4_K_M.gguf"
    [Parameter(Mandatory = $true)][string]$Model,
    [int]$Port = 8080,
    [string]$CudaDllDir = "$env:USERPROFILE\.lmstudio\extensions\backends\vendor\win-llama-cuda12-vendor-v2"
)

# LM Studio 內建的 llama-server 需要這個目錄的 CUDA runtime DLL，否則會以 exit code 53 / -1073741515 結束
if (Test-Path -LiteralPath $CudaDllDir) {
    $env:PATH = "$CudaDllDir;$env:PATH"
}

if (-not (Test-Path -LiteralPath $Model)) {
    throw "Model not found: $Model"
}

& $LlamaServer `
    --model $Model `
    --host 127.0.0.1 `
    --port $Port `
    --ctx-size 32768 `
    --parallel 1 `
    --n-gpu-layers 99 `
    --temp 1.0 `
    --top-k 20 `
    --top-p 0.95 `
    --repeat-penalty 1.0 `
    --chat-template-kwargs '{"enable_thinking":false}'
