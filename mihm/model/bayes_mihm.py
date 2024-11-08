import torch
import torch.nn as nn
import torch.nn.functional as F
import pyro
import pyro.distributions as dist
from pyro.nn import PyroModule, PyroSample
import torch.nn.init as init


class BayesMIHM(PyroModule):
    def __init__(
        self,
        interaction_var_size,
        controlled_var_size,
        hidden_layer_sizes,
        include_interactor_bias=True,
        dropout=0.5,
        svd=False,
        svd_matrix=None,
        k_dim=10,
        device="cpu",
        concatenate_interaction_vars=False,
        batch_norm=True,
    ):
        super().__init__()
        self.concatenate_interaction_vars = concatenate_interaction_vars
        if concatenate_interaction_vars:
            controlled_var_size = controlled_var_size + interaction_var_size
        self.dropout = nn.Dropout(dropout)
        self.device = device
        self.batch_norm = batch_norm
        if self.batch_norm:
            self.bn = nn.BatchNorm1d(1, affine=False)
        if svd:
            assert svd_matrix is not None
            assert k_dim <= interaction_var_size
            if not isinstance(svd_matrix, torch.Tensor):
                svd_matrix = torch.tensor(svd_matrix)
            self.svd_matrix = svd_matrix
            interaction_var_size = k_dim
            self.k_dim = k_dim
            if device == "cuda":
                self.svd_matrix = self.svd_matrix.cuda()
        self.svd = svd

        layer_input_sizes = [interaction_var_size] + hidden_layer_sizes
        self.num_layers = len(hidden_layer_sizes)
        self.layers = torch.nn.ModuleList(
            [
                nn.Linear(layer_input_sizes[i], layer_input_sizes[i + 1])
                for i in range(self.num_layers)
            ]
        )

        self.controlled_var_weights = PyroModule[nn.Linear](controlled_var_size, 1)
        self.controlled_var_weights.weight = PyroSample(
            dist.Normal(0.0, torch.tensor(1.0, device=device))
            .expand([1, controlled_var_size])
            .to_event(2)
        )
        self.controlled_var_weights.bias = PyroSample(
            dist.Normal(0.0, torch.tensor(5.0, device=device)).expand([1]).to_event(1)
        )

        self.interaction_weight = PyroSample(
            dist.HalfNormal(torch.tensor(1.0, device=device)).expand([1]).to_event(1)
        )

        self.interactor_main_weight = PyroSample(
            dist.Normal(0.0, torch.tensor(1.0, device=device)).expand([1]).to_event(1)
        )

        if not self.concatenate_interaction_vars:
            self.index_main_weight = PyroSample(
                dist.Normal(0.0, torch.tensor(1.0, device=device))
                .expand([1])
                .to_event(1)
            )

        self.include_interactor_bias = include_interactor_bias
        if include_interactor_bias:
            self.interactor_bias = PyroSample(
                dist.HalfNormal(torch.tensor(1.0, device=device))
                .expand([1])
                .to_event(1)
            )
        self.initialize_weights()
        self.to(device)

    def forward(self, interaction_input_vars, interactor_var, controlled_vars, y=None):
        if self.concatenate_interaction_vars:
            all_linear_vars = torch.cat((controlled_vars, interaction_input_vars), 1)
        else:
            all_linear_vars = controlled_vars
        controlled_term = self.controlled_var_weights(all_linear_vars)

        if self.svd:
            interaction_input_vars = torch.matmul(
                interaction_input_vars, self.svd_matrix[:, : self.k_dim]
            )

        for i, layer in enumerate(self.layers):
            if i == self.num_layers - 1:
                interaction_input_vars = layer(interaction_input_vars)
            else:
                interaction_input_vars = F.gelu(layer(interaction_input_vars))
                interaction_input_vars = self.dropout(interaction_input_vars)
        if self.batch_norm:
            predicted_index = self.bn(interaction_input_vars)
        else:
            predicted_index = interaction_input_vars

        if self.include_interactor_bias:
            interactor = torch.maximum(
                torch.tensor([[0.0]], device=self.device),
                (torch.unsqueeze(interactor_var, 1) - self.interactor_bias),
            )

        else:
            interactor = torch.unsqueeze(interactor_var, 1)

        interactor_main_term = self.interactor_main_weight * interactor

        predicted_interaction_term = (
            self.interaction_weight * interactor * predicted_index
        )

        if self.concatenate_interaction_vars:
            predicted_epi = (
                predicted_interaction_term + controlled_term + interactor_main_term
            )
        else:
            predicted_epi = (
                predicted_interaction_term
                + controlled_term
                + interactor_main_term
                + predicted_index * self.index_main_weight
            )

        sigma = pyro.sample(
            "sigma",
            dist.Gamma(torch.tensor(0.5, device=interaction_input_vars.device), 1),
        )

        with pyro.plate("data", predicted_epi.size(0)):
            obs = pyro.sample(
                "obs", dist.Normal(predicted_epi, sigma).to_event(1), obs=y
            )
        return predicted_epi

    def get_resilience_index(self, interaction_input_vars):
        if self.svd:
            interaction_input_vars = torch.matmul(
                interaction_input_vars, self.svd_matrix[:, : self.k_dim]
            )
        for i, layer in enumerate(self.layers):
            if i == self.num_layers - 1:
                interaction_input_vars = layer(interaction_input_vars)
            else:
                interaction_input_vars = F.gelu(layer(interaction_input_vars))
        predicted_index = interaction_input_vars
        return predicted_index

    def initialize_weights(self):
        for module in self.modules():
            if isinstance(module, nn.Linear):
                init.kaiming_normal_(module.weight)


class RelaxedBayesMIHM(PyroModule):
    def __init__(
        self,
        interaction_var_size,
        controlled_var_size,
        hidden_layer_sizes,
        include_interactor_bias=True,
        dropout=0.5,
        svd=False,
        svd_matrix=None,
        k_dim=10,
        device="cpu",
        concatenate_interaction_vars=False,
        batch_norm=True,
    ):
        super().__init__()
        self.concatenate_interaction_vars = concatenate_interaction_vars
        if concatenate_interaction_vars:
            controlled_var_size = controlled_var_size + interaction_var_size
        self.dropout = nn.Dropout(dropout)
        self.device = device
        self.batch_norm = batch_norm
        if self.batch_norm:
            self.bn = nn.BatchNorm1d(1, affine=False)
        if svd:
            assert svd_matrix is not None
            assert k_dim <= interaction_var_size
            if not isinstance(svd_matrix, torch.Tensor):
                svd_matrix = torch.tensor(svd_matrix)
            self.svd_matrix = svd_matrix
            interaction_var_size = k_dim
            self.k_dim = k_dim
            if device == "cuda":
                self.svd_matrix = self.svd_matrix.cuda()
        self.svd = svd

        layer_input_sizes = [interaction_var_size] + hidden_layer_sizes
        self.num_layers = len(hidden_layer_sizes)
        self.layers = torch.nn.ModuleList(
            [
                nn.Linear(layer_input_sizes[i], layer_input_sizes[i + 1])
                for i in range(self.num_layers)
            ]
        )

        self.controlled_var_weights = nn.Linear(controlled_var_size, 1)

        self.interaction_weight = PyroSample(
            dist.HalfNormal(torch.tensor(1.0, device=device)).expand([1]).to_event(1)
        )

        self.interactor_main_weight = PyroSample(
            dist.Normal(0.0, torch.tensor(1.0, device=device)).expand([1]).to_event(1)
        )

        if not self.concatenate_interaction_vars:
            self.index_main_weight = nn.Parameter(torch.randn(1, 1))

        self.include_interactor_bias = include_interactor_bias
        if include_interactor_bias:
            self.interactor_bias = PyroSample(
                dist.HalfNormal(torch.tensor(1.0, device=device))
                .expand([1])
                .to_event(1)
            )
        self.initialize_weights()
        self.to(device)

    def forward(self, interaction_input_vars, interactor_var, controlled_vars, y=None):
        if self.concatenate_interaction_vars:
            all_linear_vars = torch.cat((controlled_vars, interaction_input_vars), 1)
        else:
            all_linear_vars = controlled_vars
        controlled_term = self.controlled_var_weights(all_linear_vars)

        if self.svd:
            interaction_input_vars = torch.matmul(
                interaction_input_vars, self.svd_matrix[:, : self.k_dim]
            )

        for i, layer in enumerate(self.layers):
            if i == self.num_layers - 1:
                interaction_input_vars = layer(interaction_input_vars)
            else:
                interaction_input_vars = F.gelu(layer(interaction_input_vars))
                interaction_input_vars = self.dropout(interaction_input_vars)
        if self.batch_norm:
            predicted_index = self.bn(interaction_input_vars)
        else:
            predicted_index = interaction_input_vars

        if self.include_interactor_bias:
            interactor = torch.maximum(
                torch.tensor([[0.0]], device=self.device),
                (torch.unsqueeze(interactor_var, 1) - self.interactor_bias),
            )

        else:
            interactor = torch.unsqueeze(interactor_var, 1)

        interactor_main_term = self.interactor_main_weight * interactor

        predicted_interaction_term = (
            self.interaction_weight * interactor * predicted_index
        )

        if self.concatenate_interaction_vars:
            predicted_epi = (
                predicted_interaction_term + controlled_term + interactor_main_term
            )
        else:
            predicted_epi = (
                predicted_interaction_term
                + controlled_term
                + interactor_main_term
                + predicted_index * self.index_main_weight
            )

        sigma = pyro.sample(
            "sigma",
            dist.HalfNormal(torch.tensor(1.0, device=interaction_input_vars.device)),
        )

        with pyro.plate("data", predicted_epi.size(0)):
            obs = pyro.sample(
                "obs", dist.Normal(predicted_epi, sigma).to_event(1), obs=y
            )
        return predicted_epi

    def get_resilience_index(self, interaction_input_vars):
        if self.svd:
            interaction_input_vars = torch.matmul(
                interaction_input_vars, self.svd_matrix[:, : self.k_dim]
            )
        for i, layer in enumerate(self.layers):
            if i == self.num_layers - 1:
                interaction_input_vars = layer(interaction_input_vars)
            else:
                interaction_input_vars = F.gelu(layer(interaction_input_vars))
        predicted_index = interaction_input_vars
        return predicted_index

    def initialize_weights(self):
        for module in self.modules():
            if isinstance(module, nn.Linear):
                init.kaiming_normal_(module.weight)
