# coding=utf-8
# Copyright (c) 2022, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import torch
from einops import rearrange
from torch import einsum, nn
from nemo.utils import logging

__all__ = ['RotaryEmbedding', 'apply_rotary_pos_emb']


class RotaryEmbedding(nn.Module):
    """
    Implements Rotary Position Embedding from https://arxiv.org/abs/2104.09864.
    """

    def __init__(
        self,
        dim: int,
        seq_len_interpolation_factor: int = None,
        base_len: int = None,
        enforce_fp32_pos_idx: bool = False,
        augment_seq: bool = False,
    ):
        """
        Args:

            dim (int): rotary embedding dimension
            seq_len_interpolation_factor (int): if not None, discrete positions will be interpolated
            by this factor via the trick in https://arxiv.org/abs/2306.15595.
            pretrained_max_position_embeddings (int): pre-trained max_position_embeddings before position interpolation.
        """
        super().__init__()
        self.seq_len_interpolation_factor = seq_len_interpolation_factor
        inv_freq = 1.0 / (10000 ** (torch.arange(0, dim, 2).float() / dim))
        self.register_buffer('inv_freq', inv_freq)
        self.base_len = base_len
        self.enforce_fp32_pos_idx = enforce_fp32_pos_idx
        self.augment_seq = augment_seq

        logging.info(f'base_len: {base_len}, seq_len_interpolation_factor: {seq_len_interpolation_factor}, enforce_fp32_pos_idx: {enforce_fp32_pos_idx}, augment_seq: {augment_seq}')

    def forward(self, max_seq_len, offset=0, maybe_interpolate=True):
        if self.enforce_fp32_pos_idx:
            seq = torch.arange(max_seq_len, device=self.inv_freq.device, dtype=torch.float32) + offset
        else:
            seq = torch.arange(max_seq_len, device=self.inv_freq.device, dtype=self.inv_freq.dtype) + offset

        if self.augment_seq:
            seq += torch.rand_like(seq) 

        if self.base_len is not None and self.seq_len_interpolation_factor is not None and maybe_interpolate:
            if max_seq_len > self.base_len * self.seq_len_interpolation_factor:
                # dynamic linear scaling (length > position we have learned)
                seq *= 1 / (max_seq_len / self.base_len)
            else:
                # fixed linear scaling
                seq *= 1 / self.seq_len_interpolation_factor

        freqs = einsum('i , j -> i j', seq, self.inv_freq)
        # first part even vector components, second part odd vector components,
        #  2 * dim in dimension size
        emb = torch.cat((freqs, freqs), dim=-1)
        # emb [seq_length, .., dim]
        return rearrange(emb, 'n d -> n 1 1 d')


def _rotate_half(x):
    """
    change sign so the last dimension
    [A, B, C, D] -> [-C, -D, A, B]
    """
    x = rearrange(x, '... (j d) -> ... j d', j=2)
    x1, x2 = x.unbind(dim=-2)
    return torch.cat((-x2, x1), dim=-1)


def apply_rotary_pos_emb(t, freqs):
    """
    input tensor t is of shape [seq_length, ..., dim]
    rotary positional embeding tensor freqs is of shape [seq_length, ..., dim]
    check https://kexue.fm/archives/8265 for detailed formulas
    """
    rot_dim = freqs.shape[-1]
    # ideally t_pass is empty so rotary pos embedding is applied to all tensor t
    t, t_pass = t[..., :rot_dim], t[..., rot_dim:]
    # first part is cosine component
    # second part is sine component, need to change signs with _rotate_half method
    t = (t * freqs.cos()) + (_rotate_half(t) * freqs.sin())
    return torch.cat((t, t_pass), dim=-1)
