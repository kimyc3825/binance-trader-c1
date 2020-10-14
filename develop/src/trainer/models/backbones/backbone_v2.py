import math
import torch
import torch.nn as nn
import torch.nn.functional as F
from trainer.modules.block_1d import DenseBlock, TransitionBlock, NORMS


class BackboneV2(nn.Module):
    def __init__(
        self,
        in_channels,
        n_assets,
        n_class_y_per_asset,
        n_class_qy_per_asset,
        n_blocks=3,
        n_block_layers=6,
        growth_rate=12,
        dropout=0.0,
        channel_reduction=0.5,
        activation="relu",
        normalization="bn",
        seblock=True,
        sablock=True,
    ):
        super(BackboneV2, self).__init__()
        self.in_channels = in_channels
        self.n_assets = n_assets
        self.n_class_y_per_asset = n_class_y_per_asset
        self.n_class_qy_per_asset = n_class_qy_per_asset

        self.n_classes_y = int(n_assets * n_class_y_per_asset)
        self.n_classes_qy = int(n_assets * n_class_qy_per_asset)

        self.n_blocks = n_blocks
        self.n_block_layers = n_block_layers
        self.growth_rate = growth_rate
        self.dropout = dropout
        self.channel_reduction = channel_reduction

        self.activation = activation
        self.normalization = normalization
        self.seblock = seblock
        self.sablock = sablock

        # Build first_conv
        out_channels = 4 * growth_rate
        self.first_conv = nn.Conv1d(
            in_channels=in_channels,
            out_channels=out_channels,
            kernel_size=3,
            stride=1,
            padding=1,
            bias=False,
        )

        # Build blocks
        in_channels = out_channels

        blocks = []
        for idx in range(n_blocks):
            blocks.append(
                self._build_block(
                    in_channels=in_channels,
                    use_transition_block=True if idx != n_blocks - 1 else False,
                )
            )

            # mutate in_channels for next block
            in_channels = self._compute_out_channels(
                in_channels=in_channels,
                use_transition_block=True if idx != n_blocks - 1 else False,
            )

        self.blocks = nn.Sequential(*blocks)

        # Last layers
        self.norm = NORMS[normalization.upper()](num_channels=in_channels)
        self.act = getattr(F, activation)
        self.global_avg_pool = nn.AdaptiveAvgPool1d(1)

        self.pred_y_fc = nn.Linear(in_channels, self.n_classes_y)
        self.pred_qy_fc = nn.Linear(in_channels, self.n_classes_qy)

        # Initialize
        for m in self.modules():
            if isinstance(m, nn.Conv1d):
                n = m.kernel_size[0] * m.out_channels
                m.weight.data.normal_(0, math.sqrt(1.0 / n))

            elif isinstance(m, nn.BatchNorm1d):
                m.weight.data.fill_(1)

                if m.bias is not None:
                    m.bias.data.zero_()

            elif isinstance(m, nn.Linear):
                if m.bias is not None:
                    m.bias.data.zero_()

    def _compute_out_channels(self, in_channels, use_transition_block=True):
        if use_transition_block is True:
            return int(
                math.floor(
                    (in_channels + (self.n_block_layers * self.growth_rate))
                    * self.channel_reduction
                )
            )

        return in_channels + (self.n_block_layers * self.growth_rate)

    def _build_block(self, in_channels, use_transition_block=True):
        dense_block = DenseBlock(
            n_layers=self.n_block_layers,
            in_channels=in_channels,
            growth_rate=self.growth_rate,
            dropout=self.dropout,
            activation=self.activation,
            normalization=self.normalization,
            seblock=self.seblock,
            sablock=self.sablock,
        )

        if use_transition_block is True:
            in_channels = int(in_channels + (self.n_block_layers * self.growth_rate))
            out_channels = int(math.floor(in_channels * self.channel_reduction))
            transition_block = TransitionBlock(
                in_channels=in_channels,
                out_channels=out_channels,
                dropout=self.dropout,
                activation=self.activation,
                normalization=self.normalization,
            )

            return nn.Sequential(*[dense_block, transition_block])

        return dense_block

    def forward(self, x):
        B, _, _ = x.size()
        out = self.blocks(self.first_conv(x))
        out = self.global_avg_pool(self.act(self.norm(out))).view(B, -1)

        preds_y = self.pred_y_fc(out)
        preds_qy = self.pred_qy_fc(out)

        # out shape: (B, n_class / n_class_per_asset, n_class_per_asset)
        return (
            preds_y.view(B, -1, self.n_class_y_per_asset),
            preds_qy.view(B, -1, self.n_class_qy_per_asset),
        )