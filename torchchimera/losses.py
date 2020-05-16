import torch
from math import pi
from itertools import permutations

# loss functions for deep clustering head
# embd: (batch_size, time*freq_bin, embd_dim)
# label: (batch_size, time*freq_bin, n_channels)
def loss_dc(embd, label):
    return torch.sum(embd.transpose(1, 2).bmm(embd) ** 2) \
        + torch.sum(label.transpose(1, 2).bmm(label) ** 2) \
        - 2 * torch.sum(embd.transpose(1, 2).bmm(label) ** 2)

def loss_dc_whitend(embd, label):
    C = label.shape[2]
    D = embd.shape[2]
    VtV = embd.transpose(1, 2).bmm(embd) + 1e-24 * torch.eye(D)
    VtY = embd.transpose(1, 2).bmm(label)
    YtY = label.transpose(1, 2).bmm(label) + 1e-24 * torch.eye(C)
    return embd.shape[0] * D - torch.trace(torch.sum(
        VtV.inverse().bmm(VtY).bmm(YtY.inverse()).bmm(VtY.transpose(1, 2)),
        dim=0
    ))

# loss functions for mask inference head
# mask: (batch_size, n_channels, freq_bin, time)
# source: (batch_size, n_channels, freq_bin, time, 2)
# mixture: (batch_size, freq_bin, time, 2)
# source and mixture are obtained from torch.stft
def loss_mi_msa(mask, mixture, sources):
    C = mask.shape[1]
    abs_comp = lambda X: torch.sqrt(torch.sum(X**2, -1).clamp(min=1e-12))
    phase_comp = lambda X: torch.atan2(*X.split(1, dim=-1)[::-1]).squeeze()
    abs_X = abs_comp(mixture)
    abs_S = abs_comp(sources)
    return sum(min(
        torch.sum((torch.stack(M) * _abs_X - _abs_S) ** 2)
        for M in permutations(_mask)
    ) for _mask, _abs_X, _abs_S in zip(mask, abs_X, abs_S))

def loss_mi_tpsa(mask, mixture, sources, gamma=1, L=1):
    C = mask.shape[1]
    abs_comp = lambda X: torch.sqrt(torch.sum(X**2, -1).clamp(min=1e-12))
    phase_comp = lambda X: torch.atan2(*X.split(1, dim=-1)[::-1]).squeeze(-1)
    abs_X = abs_comp(mixture.unsqueeze(1))
    phase_X = phase_comp(mixture.unsqueeze(1))
    abs_S = abs_comp(sources)
    phase_S = phase_comp(sources)
    spectrum = torch.min(
        input=torch.max(
            input=abs_S * torch.cos(phase_S - phase_X),
            other=torch.zeros_like(abs_S)
        ),
        other=gamma*abs_X
    )

    if L == 1:
        return sum(min(
            torch.sum(torch.abs(torch.stack(M) * _abs_X - _spectrum))
            for M in permutations(_mask)
        ) for _mask, _abs_X, _spectrum in zip(mask, abs_X, spectrum))
    elif L == 2:
        return sum(min(
            torch.sum((torch.stack(M) * _abs_X - _spectrum) ** L)
            for M in permutations(_mask)
        ) for _mask, _abs_X, _spectrum in zip(mask, abs_X, spectrum))
    else:
        raise NotImplementedError()

# loss for waveform approximation
# source_pred: (batch_size, n_channels, waveform_length)
# source_true: (batch_size, n_channels, waveform_length)
def loss_wa(source_pred, source_true):
    return sum(min(
            torch.sum(torch.abs(torch.stack(s) - _source_true))
            for s in permutations(_source_pred)
        ) for _source_pred, _source_true in zip(source_pred, source_true))

# loss for cross entropy phasebook
# phase_prob_pred: (batch_size, n_channels, freq_bin, time, book_size)
# phase_true: (batch_size, n_channels, freq_bin, time, 2) (complex) or
#             (batch_size, n_channels, freq_bin, time) (phase in radias)
# phasebook: phasebook
def loss_ce_phase(phase_prob_pred, phase_true, phasebook):
    if len(phase_true.shape) == 5: # treat as complex number
        phase_true_norm = phase_true\
            / torch.sqrt(torch.sum(phase_true**2, dim=-1).clamp(min=1e-12))\
            .unsqueeze(-1)
        phase_true = torch.atan2(*phase_true_norm.split(1, dim=-1)[::-1])
        phase_true = phase_true.squeeze(-1)
    phase_ref_idx = torch.argmin(
        torch.min(
            (phasebook.view(1,1,1,1,-1)-phase_true.unsqueeze(-1)) % (2*pi),
            (phase_true.unsqueeze(-1)-phasebook.view(1,1,1,1,-1)) % (2*pi)
        ),
        dim=-1
    )
    return sum(min(
        torch.sum(-torch.log(
            torch.stack(p).gather(-1, _ref_idx.unsqueeze(-1))
            .squeeze(-1).clamp(min=1e-36)
        ))
        for p in permutations(_prob)
    ) for _prob, _ref_idx in zip(phase_prob_pred, phase_ref_idx))

# loss function for spectrogram (complex domain)
# com_pred: (batch_size, n_channels, freq_bin, spec_time, 2)
# mixture: (batch_size, freq_bin, spec_time, 2)
# sources: (batch_size, n_channels, freq_bin, spec_time, 2)
def loss_csa(com_pred, mixture, sources, L=1):
    comp_mul = lambda X, Y: torch.stack(
        (X.unbind(-1)[0] * Y.unbind(-1)[0] - X.unbind(-1)[1] * Y.unbind(-1)[1],
         X.unbind(-1)[0] * Y.unbind(-1)[1] + X.unbind(-1)[1] * Y.unbind(-1)[0]),
        dim=-1
    )
    abs_comp = lambda X: torch.sqrt(torch.sum(X**2, -1).clamp(min=1e-12))
    phase_comp = lambda X: torch.atan2(*X.split(1, dim=-1)[::-1]).squeeze(-1)
    source_pred = comp_mul(com_pred, mixture.unsqueeze(1))

    if L == 1:
        return sum(min(
            torch.sum(abs_comp(torch.stack(s) - _sources))
            for s in permutations(_source_pred)
        ) for _source_pred, _sources in zip(source_pred, sources))
    elif L == 2:
        return sum(min(
            torch.sum(abs_comp(torch.stack(s) - _sources) ** L)
            for s in permutations(_source_pred)
        ) for _source_pred, _sources in zip(source_pred, sources))
    else:
        raise NotImplementedError()
