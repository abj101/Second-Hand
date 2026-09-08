from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable


def _zeros(n: int) -> list[float]:
    return [0.0] * n


def _rand_mat(rows: int, cols: int, scale: float = 0.05) -> list[list[float]]:
    return [[random.uniform(-scale, scale) for _ in range(cols)] for _ in range(rows)]


def _matvec(mat: list[list[float]], vec: list[float]) -> list[float]:
    return [sum(row[i] * vec[i] for i in range(len(vec))) for row in mat]


def _relu(vec: list[float]) -> list[float]:
    return [x if x > 0 else 0.0 for x in vec]


class LinearQ:
    """Tiny MLP Q-network. Stdlib only so tests never need torch."""

    def __init__(self, n_obs: int, n_act: int, hidden: int = 16) -> None:
        self.n_obs = n_obs
        self.n_act = n_act
        self.hidden = hidden
        self.w1 = _rand_mat(hidden, n_obs)
        self.b1 = _zeros(hidden)
        self.w2 = _rand_mat(n_act, hidden)
        self.b2 = _zeros(n_act)

    def q(self, obs: list[float]) -> list[float]:
        h = _relu([s + b for s, b in zip(_matvec(self.w1, obs), self.b1)])
        return [s + b for s, b in zip(_matvec(self.w2, h), self.b2)]

    def clone_from(self, other: "LinearQ") -> None:
        self.w1 = [row[:] for row in other.w1]
        self.b1 = other.b1[:]
        self.w2 = [row[:] for row in other.w2]
        self.b2 = other.b2[:]

    def sgd(self, obs: list[float], action: int, target: float, lr: float = 0.01) -> float:
        q_vals = self.q(obs)
        pred = q_vals[action]
        err = pred - target
        h = _relu([s + b for s, b in zip(_matvec(self.w1, obs), self.b1)])
        for j in range(self.n_act):
            grad = err if j == action else 0.0
            self.b2[j] -= lr * grad
            for i in range(self.hidden):
                self.w2[j][i] -= lr * grad * h[i]
        dh = [0.0] * self.hidden
        for i in range(self.hidden):
            if h[i] > 0:
                dh[i] = err * self.w2[action][i]
        for i in range(self.hidden):
            self.b1[i] -= lr * dh[i]
            for k in range(self.n_obs):
                self.w1[i][k] -= lr * dh[i] * obs[k]
        return err * err


@dataclass
class DoubleDQN:
    n_obs: int
    n_act: int
    gamma: float = 0.99
    epsilon: float = 0.2
    hidden: int = 16
    replay: list[tuple[list[float], int, float, list[float], bool]] = field(default_factory=list)
    max_replay: int = 2048

    def __post_init__(self) -> None:
        self.online = LinearQ(self.n_obs, self.n_act, self.hidden)
        self.target = LinearQ(self.n_obs, self.n_act, self.hidden)
        self.target.clone_from(self.online)

    def act(self, obs: list[float], *, greedy: bool = False) -> int:
        if not greedy and random.random() < self.epsilon:
            return random.randrange(self.n_act)
        q_vals = self.online.q(obs)
        return max(range(self.n_act), key=lambda i: q_vals[i])

    def remember(
        self,
        obs: list[float],
        action: int,
        reward: float,
        nxt: list[float],
        done: bool,
    ) -> None:
        self.replay.append((list(obs), action, reward, list(nxt), done))
        if len(self.replay) > self.max_replay:
            self.replay.pop(0)

    def train_step(self, batch_size: int = 16) -> float:
        if len(self.replay) < 4:
            return 0.0
        batch = random.sample(self.replay, min(batch_size, len(self.replay)))
        loss = 0.0
        for obs, action, reward, nxt, done in batch:
            q_next_online = self.online.q(nxt)
            a_star = max(range(self.n_act), key=lambda i: q_next_online[i])
            q_next = self.target.q(nxt)[a_star]
            target = reward if done else reward + self.gamma * q_next
            loss += self.online.sgd(obs, action, target)
        return loss / len(batch)

    def sync_target(self) -> None:
        self.target.clone_from(self.online)

    def act_name(self, obs: list[float], names: list[str], *, greedy: bool = True) -> str:
        idx = self.act(obs, greedy=greedy)
        return names[idx]


def encode_extracted(boxes: list[Any], dim: int = 16) -> list[float]:
    vec = [0.0] * dim
    if not boxes:
        return vec
    n = float(len(boxes))
    vec[0] = n
    vec[1] = sum(getattr(b, "cx", 0.0) for b in boxes) / n
    vec[2] = sum(getattr(b, "y", 0.0) for b in boxes) / n
    active = min(boxes, key=lambda b: getattr(b, "y", 0.0))
    vec[3] = float(getattr(active, "cx", 0.0))
    vec[4] = float(getattr(active, "y", 0.0))
    cols = max(1, dim - 5)
    for b in boxes:
        cx = float(getattr(b, "cx", 0.0))
        idx = min(cols - 1, max(0, int(cx * cols)))
        vec[5 + idx] += 1.0 / n
    return vec


def encode_pixels(frame: Any, dim: int = 16) -> list[float]:
    data = getattr(frame, "data", None)
    if not data:
        return [0.0] * dim
    raw = data[: dim * 8] if isinstance(data, (bytes, bytearray)) else bytes(dim)
    out = [0.0] * dim
    for i, b in enumerate(raw):
        out[i % dim] += b / 255.0
    n = max(1.0, len(raw) / dim)
    return [x / n for x in out]
