import abc
import itertools
from torch import nn
from torch.nn import functional as F
from torch import optim

import numpy as np
import torch
from torch import distributions

from cs285.infrastructure import pytorch_util as ptu
from cs285.policies.base_policy import BasePolicy
from cs285.infrastructure.utils import normalize


class MLPPolicy(BasePolicy, nn.Module, metaclass=abc.ABCMeta):

    def __init__(self,
                 ac_dim,
                 ob_dim,
                 n_layers,
                 size,
                 discrete=False,
                 learning_rate=1e-4,
                 training=True,
                 nn_baseline=False,
                 normal=True,
                 **kwargs
                 ):
        super().__init__(**kwargs)

        # init vars
        self.ac_dim = ac_dim
        self.ob_dim = ob_dim
        self.n_layers = n_layers
        self.discrete = discrete
        self.size = size
        self.learning_rate = learning_rate
        self.training = training
        self.nn_baseline = nn_baseline
        self.normal = normal
        if self.discrete:
            self.logits_na = ptu.build_mlp(input_size=self.ob_dim,
                                           output_size=self.ac_dim,
                                           n_layers=self.n_layers,
                                           size=self.size)
            self.logits_na.to(ptu.device)
            self.mean_net = None
            self.logstd = None
            self.optimizer = optim.Adam(self.logits_na.parameters(),
                                        self.learning_rate)
        else:
            if self.normal:
                self.logits_na = None
                self.mean_net = ptu.build_mlp(input_size=self.ob_dim,
                                          output_size=self.ac_dim,
                                          n_layers=self.n_layers, size=self.size)
                self.logstd = nn.Parameter(
                    torch.zeros(self.ac_dim, dtype=torch.float32, device=ptu.device)
                )
                self.mean_net.to(ptu.device)
                self.logstd.to(ptu.device)
                self.optimizer = optim.Adam(
                    itertools.chain([self.logstd], self.mean_net.parameters()),
                    self.learning_rate
                )
            else:
                self.logalpha = ptu.build_mlp(input_size=self.ob_dim,
                                          output_size=self.ac_dim,
                                          n_layers=self.n_layers, size=self.size)
                self.logbeta = ptu.build_mlp(input_size=self.ob_dim,
                                          output_size=self.ac_dim,
                                          n_layers=self.n_layers, size=self.size)
                self.logalpha.to(ptu.device)
                self.logbeta.to(ptu.device)
                self.optimizer = optim.Adam(
                    itertools.chain(self.logalpha.parameters(), self.logbeta.parameters()),
                    self.learning_rate
                )

        if nn_baseline:
            self.baseline = ptu.build_mlp(
                input_size=self.ob_dim,
                output_size=1,
                n_layers=self.n_layers,
                size=self.size,
            )
            self.baseline.to(ptu.device)
            self.baseline_optimizer = optim.Adam(
                self.baseline.parameters(),
                self.learning_rate,
            )
        else:
            self.baseline = None

    ##################################

    def save(self, filepath):
        torch.save(self.state_dict(), filepath)

    ##################################

    # query the policy with observation(s) to get selected action(s)
    def get_action(self, obs: np.ndarray) -> np.ndarray:
        if len(obs.shape) > 1:
            observation = obs
        else:
            observation = obs[None]

        observation = ptu.from_numpy(observation)
        action = self(observation).sample()

        return ptu.to_numpy(action)

    # update/train this policy
    def update(self, observations, actions, **kwargs):
        raise NotImplementedError

    # This function defines the forward pass of the network.
    # You can return anything you want, but you should be able to differentiate
    # through it. For example, you can return a torch.FloatTensor. You can also
    # return more flexible objects, such as a
    # `torch.distributions.Distribution` object. It's up to you!
    def forward(self, observation: torch.FloatTensor):
        if self.discrete:
            logits = self.logits_na(observation)
            action_distribution = distributions.Categorical(logits=logits)
        else:
            if self.normal:
                batch_mean = self.mean_net(observation)
                scale_tril = torch.diag(torch.exp(self.logstd))
                batch_dim = batch_mean.shape[0]
                batch_scale_tril = scale_tril.repeat(batch_dim, 1, 1)
                action_distribution = distributions.MultivariateNormal(
                    batch_mean,
                    scale_tril=batch_scale_tril,
                )
            else:
                action_distribution = distributions.beta.Beta(
                    torch.FloatTensor(torch.exp(self.logalpha(observation))+1),
                    torch.FloatTensor(torch.exp(self.logbeta(observation))+1)
                )
        return action_distribution


#####################################################
#####################################################

class MLPPolicyPG(MLPPolicy):
    def __init__(self, ac_dim, ob_dim, n_layers, size, **kwargs):

        super().__init__(ac_dim, ob_dim, n_layers, size, **kwargs)
        self.baseline_loss = nn.MSELoss()

    def update(self, observations, acs_na, adv_n=None, acs_labels_na=None,
               qvals=None):
        observations = ptu.from_numpy(observations)
        actions = ptu.from_numpy(acs_na)
        adv_n = ptu.from_numpy(adv_n)

        action_distribution = self(observations)
        if self.normal:
            distribution_log_prob = action_distribution.log_prob(actions)
        else:
            distribution_log_prob = torch.sum(action_distribution.log_prob(actions), axis=1)
        loss = - distribution_log_prob * adv_n
        loss = loss.mean()
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

        if self.nn_baseline:
            targets_n = normalize(qvals, np.mean(qvals), np.std(qvals))
            targets_n = ptu.from_numpy(targets_n)
            baseline_predictions = self.baseline(observations).squeeze()
            assert baseline_predictions.dim() == baseline_predictions.dim()

            baseline_loss = F.mse_loss(baseline_predictions, targets_n)
            self.baseline_optimizer.zero_grad()
            baseline_loss.backward()
            self.baseline_optimizer.step()

        return {
            'Training Loss': ptu.to_numpy(loss),
        }

    def run_baseline_prediction(self, obs):
        """
            Helper function that converts `obs` to a tensor,
            calls the forward method of the baseline MLP,
            and returns a np array

            Input: `obs`: np.ndarray of size [N, 1]
            Output: np.ndarray of size [N]

        """
        obs = ptu.from_numpy(obs)
        predictions = self.baseline(obs)
        return ptu.to_numpy(predictions)[:, 0]


class MLPPolicyAC(MLPPolicy):
    def update(self, observations, actions, adv_n=None):

        observations = ptu.from_numpy(observations)
        actions = ptu.from_numpy(actions)
        adv_n = ptu.from_numpy(adv_n)

        # TODO: update the policy and return the loss
        action_distribution = self(observations)
        if self.normal:
            distribution_log_prob = action_distribution.log_prob(actions)
        else:
            distribution_log_prob = torch.sum(action_distribution.log_prob(actions), axis=1)
        loss = - distribution_log_prob * adv_n
        loss = loss.mean()

        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

        return loss.item()