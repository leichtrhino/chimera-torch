#!/usr/bin/env python

import sys
import math
import torch
import torchaudio
from datasets import DSD100, MixTransform
from models import ChimeraClassic
from models import ChimeraPlusPlus
from models import ChimeraMagPhasebook
from losses import loss_mi_tpsa, loss_dc_whitend, loss_wa, loss_ce_phase, loss_csa
from layers import MisiLayer

def main():
    batch_size = 32
    orig_freq = 44100
    target_freq = 16000
    seconds = 5

    n_fft = 512
    win_length = 512
    hop_length = 128
    freq_bins, spec_time, _ = torch.stft(
        torch.Tensor(seconds * target_freq), n_fft, hop_length, win_length
    ).shape

    dataset = DSD100(
        '/Volumes/Buffalo 2TB/Datasets/DSD100', 'Dev', seconds * orig_freq)
    dataloader = torch.utils.data.DataLoader(
        dataset, batch_size=batch_size, num_workers=8, shuffle=True)
    transforms = [
        MixTransform([(0, 1, 2), 3, (0, 1, 2, 3)]),
        lambda x: x.reshape(x.shape[0] * 3, seconds * orig_freq),
        torchaudio.transforms.Resample(orig_freq, target_freq),
        lambda x: x.reshape(x.shape[0] // 3, 3, seconds * target_freq),
    ]
    def transform(x):
        for t in transforms:
            x = t(x)
        return x

    my_stft = lambda x: torch.stft(
            x.reshape(x.shape[:-1].numel(), seconds * target_freq),
            n_fft, hop_length, win_length
        ).reshape(*x.shape[:-1], freq_bins, spec_time, 2)
    my_istft = lambda X: torchaudio.istft(
        X, n_fft, hop_length, win_length
    )

    initial_epoch = 9 # start at 0
    train_epoch = 11
    loss_function = 'chimera++' # 'chimera++', 'mask', 'wave'
    model = ChimeraMagPhasebook(freq_bins, spec_time, 2, 20, N=600)
    if initial_epoch > 0:
        model.load_state_dict(torch.load(f'model_epoch{initial_epoch-1}.pth'))
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-4)

    for epoch in range(initial_epoch, initial_epoch+train_epoch):
        sum_loss = 0
        total_batch = 0
        last_output_len = 0
        for step, batch in enumerate(dataloader):
            batch = transform(batch)
            x, s = batch[:, 2, :], batch[:, :2, :]
            X, S = my_stft(x), my_stft(s)
            X_abs = torch.sqrt(torch.sum(X**2, dim=-1))
            X_phase = X / X_abs.clamp(min=1e-12).unsqueeze(-1)
            S_abs = torch.sqrt(torch.sum(S**2, dim=-1))
            S_phase = S / S_abs.clamp(min=1e-12).unsqueeze(-1)
            Y = torch.eye(2)[
                torch.argmax(S_abs, dim=1)
                .reshape(batch.shape[0], freq_bins*spec_time)
            ]

            embd, (mask, phasep, com) = model(
                torch.log10(X_abs.clamp(min=1e-12)),
                outputs=['mag', 'phasep', 'com']
            )

            # compute loss
            if loss_function == 'chimera++':
                loss = 0.975 * loss_dc_whitend(embd, Y) \
                    + 0.025 * (
                        loss_mi_tpsa(mask, X, S, gamma=2.)
                        + loss_ce_phase(
                            phasep, S_phase, model.phase_head.codebook
                        )
                        + loss_csa(com, X, S)
                    )
            elif loss_function == 'mask':
                loss = loss_mi_tpsa(mask, X, S, gamma=2.) \
                + loss_ce_phase(
                    phasep, S_phase, model.phase_head.codebook
                ) \
                + loss_csa(com, X, S)
            elif loss_function == 'wave': # FIXME
                com_mag = torch.sqrt(torch.sum(com**2, dim=-1).clamp(min=1e-24))
                com_phase = com / com_mag.unsqueeze(-1)
                amphat = com_mag * X_abs.unsqueeze(1)
                #phasehat = X_phase.unsqueeze(1)
                phasehat = com / com_mag.unsqueeze(-1)
                l = MisiLayer(n_fft, hop_length, win_length, layer_num=5)
                phasehat, shat = l(amphat, phasehat, x)
                loss = loss_wa(shat, s)

            sum_loss += loss.item()
            total_batch += batch.shape[0]
            ave_loss = sum_loss / total_batch

            # Zero gradients, perform a backward pass, and update the weights.
            loss.backward()
            optimizer.step()
            sum_grad = sum(
                torch.sum(torch.abs(p.grad))
                for p in model.parameters() if p.grad is not None
            )
            optimizer.zero_grad()

            # Print learning statistics
            curr_output =\
                f'\repoch {epoch} step {step} loss={ave_loss} grad={sum_grad}'
            sys.stdout.write('\r' + ' ' * last_output_len)
            sys.stdout.write(curr_output)
            sys.stdout.flush()
            last_output_len = len(curr_output)

        curr_output =\
            f'\repoch {epoch} loss={ave_loss}'
        sys.stdout.write('\r' + ' ' * last_output_len)
        sys.stdout.write(f'\repoch {epoch} loss={ave_loss}\n')

        torch.save(
            model.state_dict(),
            f'model_epoch{epoch}.pth'
        )


if __name__ == '__main__':
    main()
