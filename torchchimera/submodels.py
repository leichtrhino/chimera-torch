
import torch
import torchaudio
from math import ceil, pi

class MisiLayer(torch.nn.Module):
    def __init__(self, n_fft, hop_length, win_length):
        super(MisiLayer, self).__init__()
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.win_length = win_length
    # input: (batch_size * n_channels, freq_bins, spec_time, 2)
    # input: (batch_size * n_channels, freq_bins, spec_time, 1)
    # input: (batch_size, waveform_length)
    # output: (batch_size * n_channels, freq_bins, spec_time, 2)
    def forward(self, Shat, Shatmag, mixture):
        batch_size, waveform_length = mixture.shape
        n_channels, freq_bins, spec_time, _ = Shat.shape
        n_channels //= batch_size
        waveform_length = mixture.shape[-1]

        stft = lambda x: torch.stft(
            x, self.n_fft, self.hop_length, self.win_length,
            window=torch.hann_window(self.n_fft)
        )
        istft = lambda X: torchaudio.functional.istft(
            X, self.n_fft, self.hop_length, self.win_length,
            window=torch.hann_window(self.n_fft)
        )

        shat = istft(Shat)
        delta = mixture - torch.sum(
            shat.reshape(batch_size, n_channels, waveform_length), dim=1
        ) / n_channels
        tmp = stft(shat + delta.repeat_interleave(n_channels, 0))
        phase = torch.nn.functional.normalize(tmp, p=2, dim=-1)
        return Shatmag * phase

class MisiNetwork(torch.nn.Module):
    def __init__(self, n_fft, hop_length, win_length, layer_num=1):
        super(MisiNetwork, self).__init__()
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.win_length = win_length
        self.layer_num = layer_num
    # input: (batch_size, n_channels, freq_bins, spec_time, 2)
    # input: (batch_size, waveform_length)
    # output: (batch_size, n_channels, waveform_length)
    def forward(self, mask, mixture):
        if len(mask.shape) == 4:
            # assumes real mask. zero imaginary part
            mask = torch.stack((mask, torch.zeros_like(mask)), dim=-1)
        batch_size, n_channels, freq_bins, spec_time, _ = mask.shape
        mask = mask.reshape(batch_size * n_channels, freq_bins, spec_time, _)
        waveform_length = mixture.shape[-1]
        def comp_mul(X, Y):
            xre, xim = X.unbind(-1)
            yre, yim = Y.unbind(-1)
            return torch.stack(
                (xre*yre - xim*yim, xre*yim + xim*yre), dim=-1
            )

        stft = lambda x: torch.stft(
            x.reshape(x.shape[:-1].numel(), waveform_length),
            self.n_fft, self.hop_length, self.win_length,
            window=torch.hann_window(self.n_fft)
        )
        istft = lambda X: torchaudio.functional.istft(
            X, self.n_fft, self.hop_length, self.win_length,
            window=torch.hann_window(self.n_fft)
        ).reshape(*X.shape[:-3], waveform_length)

        Shat = comp_mul(mask, stft(mixture).repeat_interleave(n_channels, 0))
        Shatmag = Shat.norm(2, -1, keepdim=True)
        for _ in range(self.layer_num):
            Shat = MisiLayer(self.n_fft, self.hop_length, self.win_length)\
                (Shat, Shatmag, mixture)
        return istft(Shat).reshape(batch_size, n_channels, waveform_length)

def _generate_dft_matrix(n_fft):
    phi = 2*pi*torch.arange(n_fft, dtype=torch.float) / n_fft
    basis = torch.arange(n_fft // 2 + 1, dtype=torch.float).unsqueeze(-1)
    return torch.cat((torch.cos(phi*basis), torch.sin(phi*basis)))

class TrainableStftLayer(torch.nn.Module):
    # XXX: padding amount in Conv1d
    def __init__(self, n_fft):
        super(TrainableStftLayer, self).__init__()
        self.n_fft = n_fft
        self.hop_length = n_fft // 4
        self.conv = torch.nn.Conv1d(
            1, self.n_fft+2, self.n_fft, stride=self.hop_length, bias=False,
            padding=self.hop_length * 2)

        weight = torch.hann_window(n_fft) * _generate_dft_matrix(n_fft)
        with torch.no_grad():
            self.conv.weight.copy_(weight.unsqueeze(1))

    # input: (batch_size * n_channels, 1, waveform_length)
    # output: (batch_size * n_channels, 2*(n_fft//2+1), time)
    def forward(self, x):
        return self.conv(x)

class TrainableIstftLayer(torch.nn.Module):
    # XXX: padding amount in ConvTranspose1d
    def __init__(self, n_fft):
        super(TrainableIstftLayer, self).__init__()
        self.n_fft = n_fft
        self.hop_length = n_fft // 4
        self.pad_windows = ceil(
            (self.n_fft - self.hop_length) / self.hop_length
        )
        self.conv = torch.nn.ConvTranspose1d(
            self.n_fft+2, 1, self.n_fft, stride=self.hop_length, bias=False,
            padding=self.hop_length * 2
        )

        weight = torch.hann_window(n_fft) * _generate_dft_matrix(n_fft)
        with torch.no_grad():
            self.conv.weight.copy_(weight.unsqueeze(1))

    # input: (batch_size * n_channels, 2*(n_fft//2+1), time)
    # output: (batch_size * n_channels, 1, waveform_length)
    def forward(self, x):
        return self.conv(x) / self.n_fft

class TrainableMisiLayer(torch.nn.Module):
    def __init__(self, n_fft):
        super(TrainableMisiLayer, self).__init__()
        self.n_fft = n_fft
        self.hop_length = n_fft // 4
        self.win_length = n_fft
        self.stft_layer = TrainableStftLayer(n_fft)
        self.istft_layer = TrainableIstftLayer(n_fft)
    # input: (batch_size * n_channels, 2*(n_fft//2+1), spec_time)
    # input: (batch_size * n_channels, 2*(n_fft//2+1), spec_time)
    #        this input is obtained by mag.repeat(1, 2, 1)
    # input: (batch_size, waveform_length)
    # output: (batch_size * n_channels, 2*(n_fft//2+1), spec_time)
    def forward(self, Shat, Shatmag, mixture):
        batch_size, waveform_length = mixture.shape
        n_channels, _, spec_time = Shat.shape
        n_channels //= batch_size

        shat = self.istft_layer(Shat) # : (B*C, 1, W)
        delta = mixture - torch.sum(
            shat.reshape(batch_size, n_channels, waveform_length), dim=1
        ) / n_channels # : (B, W)
        tmp = self.stft_layer(
            shat + delta.repeat_interleave(n_channels, 0).unsqueeze(1)
        ) # : (B*C, 2*F, T)
        phase = torch.nn.functional.normalize(
            tmp.reshape(batch_size*n_channels, 2, self.n_fft//2+1, spec_time),
            p=2, dim=1
        ).reshape(batch_size*n_channels, self.n_fft+2, spec_time)
        return Shatmag * phase

class TrainableMisiNetwork(torch.nn.Module):
    def __init__(self, n_fft, layer_num=1):
        super(TrainableMisiNetwork, self).__init__()
        self.n_fft = n_fft
        self.hop_length = n_fft // 4
        self.win_length = n_fft
        self.stft_layer = TrainableStftLayer(n_fft)
        self.istft_layer = TrainableIstftLayer(n_fft)
        self.misi_layers = torch.nn.ModuleList([
            TrainableMisiLayer(n_fft) for _ in range(layer_num)
        ])
    # input: (batch_size, n_channels, freq_bins, spec_time, 2)
    # input: (batch_size, waveform_length)
    # output: (batch_size, n_channels, waveform_length)
    def forward(self, mask, mixture):
        if len(mask.shape) == 4:
            # assumes real mask. zero imaginary part
            mask = torch.stack((mask, torch.zeros_like(mask)), dim=-1)
        batch_size, n_channels, freq_bins, spec_time, _ = mask.shape
        waveform_length = mixture.shape[-1]
        mask = mask.permute(0, 1, 4, 2, 3)\
                   .reshape(batch_size * n_channels, 2, freq_bins, spec_time)
        def comp_mul(X, Y):
            xre, xim = X.unbind(1)
            yre, yim = Y.unbind(1)
            return torch.stack(
                (xre*yre - xim*yim, xre*yim + xim*yre), dim=1
            )

        X = self.stft_layer(mixture.unsqueeze(1))\
                .reshape(batch_size, 2, freq_bins, spec_time)\
                .repeat_interleave(n_channels, 0)
        Shat = comp_mul(mask, X)
        Shatmag = Shat.norm(2, 1).repeat(1, 2, 1)
        Shat = Shat.reshape(batch_size*n_channels, 2*freq_bins, spec_time)
        for layer in self.misi_layers:
            Shat = layer(Shat, Shatmag, mixture)
        return self.istft_layer(Shat)\
                   .reshape(batch_size, n_channels, waveform_length)

