"""
Functions that use multiple times
"""

from torch import nn
import torch
import torch.multiprocessing as mp
import numpy as np
import matplotlib.pyplot as plt
import pickle
import argparse


def v_wrap(np_array, dtype=np.float32):
    if np_array.dtype != dtype:
        np_array = np_array.astype(dtype)
    return torch.from_numpy(np_array)


def set_init(layers):
    for layer in layers:
        nn.init.normal_(layer.weight, mean=0., std=0.1)
        nn.init.constant_(layer.bias, 0.)


def push_and_pull(opt, lnet, gnet, done, s_, bs, ba, br, gamma):
    if done:
        v_s_ = 0.               # terminal
    else:
        v_s_ = lnet.forward(s_)[1].data.numpy()[0, 0]

    buffer_v_target = []
    for r in br[::-1]:    # reverse buffer r
        v_s_ = r + gamma * v_s_
        buffer_v_target.append(v_s_)
    buffer_v_target.reverse()

    loss = lnet.loss_func(
        v_wrap(np.vstack(bs)),
        v_wrap(np.array(ba), dtype=np.int64) if ba[0].dtype == np.int64 else v_wrap(np.vstack(ba)),
        v_wrap(np.array(buffer_v_target)[:, None]))


    # calculate local gradients and push local parameters to global
    opt.zero_grad()
    loss.backward()

    # push and pull global parameters
    for lp, gp in zip(lnet.parameters(), gnet.parameters()):
        gp._grad = lp.grad

    opt.step()
    lnet.load_state_dict(gnet.state_dict())


def optimize(opt, lnet, done, s_, bs, ba, br, gamma):
    if done:
        v_s_ = 0.               # terminal
    else:
        v_s_ = lnet.forward(s_)[1].data.numpy()[0, 0]

    buffer_v_target = []

    for r in br[::-1]:    # reverse buffer r
        # Advantage function?
        v_s_ = r + gamma * v_s_
        buffer_v_target.append(v_s_)
    buffer_v_target.reverse()

    loss = lnet.loss_func(
        v_wrap(np.vstack(bs)),
        v_wrap(np.array(ba), dtype=np.int64) if ba[0].dtype == np.int64 else v_wrap(np.vstack(ba)),
        v_wrap(np.array(buffer_v_target)[:, None]))
    # calculate local gradients
    opt.zero_grad()
    loss.backward()
    opt.step()


def record(global_ep, global_ep_r, ep_r, res_queue, time_queue, global_time_done, time_done, name):
    with global_ep.get_lock():
        global_ep.value += 1

    if handleArguments().normalized_plot:
        with global_ep_r.get_lock():
            if global_ep_r.value == 0.:
                global_ep_r.value = ep_r
            else:
                global_ep_r.value = global_ep_r.value * 0.99 + ep_r * 0.01
            time_done = global_time_done.value * 0.99 + time_done * 0.01
        print(name, "Ep:", global_ep.value, "| Normalized Reward: %.0f" % global_ep_r.value, "| Normalized Duration:",
              round(time_done, 5))
    else:
        with global_ep_r.get_lock():
            global_ep_r.value = ep_r
        print(name, "Ep:", global_ep.value, "| Episode Reward: %.0f" % global_ep_r.value, "| Duration:",
              time_done)
    res_queue.put(global_ep_r.value)
    time_queue.put(time_done)


def plotter_ep_rew_norm(ax2, scores):
    ax2.plot(scores)
    ax2.axhline(y=0, color='r')
    ax2.set_ylim(-120,500)
    ax2.set_ylabel('Reward per Episode')
    ax2.set_xlabel('Episode')

def plotter_ep_rew(ax2, scores):
    ax2.plot(scores)
    ax2.axhline(y=0, color='r')
    ax2.set_ylim(-120,1000)
    ax2.set_ylabel('Reward per Episode')
    ax2.set_xlabel('Episode')

def plotter_ep_time_norm(ax1, duration_episode):
    ax1.plot(duration_episode)
    ax1.set_ylim(0,2)
    ax1.set_ylabel('Duration of Episode')

def plotter_ep_time(ax1, duration_episode):
    ax1.plot(duration_episode)
    ax1.set_ylim(0,20)
    ax1.set_ylabel('Duration of Episode')


def confidence_intervall(actions, load_model=False):
    count = 1
    probab_count = 0
    probabilities = []
    for a in actions:
        if a == 1:
            probab_count += 1
        if count % 10 == 0 and count != 1:
            probab_count = probab_count / 100
            probabilities.append(probab_count)
            probab_count = 0
        count += 1

    # Check for probabilities of actions and create confidence intervall for two standard deviations
    print("Probabilities: ", probabilities)

    if handleArguments().load_model:
        probs = np.asarray([probabilities])
        np.savetxt('VIZDOOM/doom_save_plot_data/probs_test.csv', probs, delimiter=',')
    else:
        probs = np.asarray([probabilities])
        np.savetxt('VIZDOOM/doom_save_plot_data/probs.csv', probs, delimiter=',')

    plt.figure(3)
    plt.plot(probabilities)
    plt.ylim(0,1)
    plt.axhline(max(probabilities), color='r')
    plt.axhline(min(probabilities), color='r')
    plt.ylabel("Probability of Choosing Action 'Shoot'")
    plt.xlabel("Batch of 100 Actions")

    stan_dev1 = np.sqrt(probabilities[1] * (1-probabilities[1]) / 100)*2
    print ("First Confidence Intervall for 95% confidence: the action 'attack' is chosen between", round(probabilities[1]-stan_dev1, 3), " and", round(probabilities[1] + stan_dev1, 3))

    stan_dev2 = np.sqrt(probabilities[2] * (1 - probabilities[2]) /100) * 2
    print("Second Confidence Intervall for 95% confidence: the action 'attack' is chosen between", round(probabilities[2] - stan_dev2, 3), " and",
          round(probabilities[2] + stan_dev2, 3))

    stan_dev3 = np.sqrt(probabilities[3] * (1 - probabilities[3]) /100) * 2
    print("Second Confidence Intervall for 95% confidence: the action 'attack' is chosen between", round(probabilities[3] - stan_dev3, 3), " and",
          round(probabilities[3] + stan_dev3, 3))


def handleArguments():
    """Handles CLI arguments and saves them globally"""
    parser = argparse.ArgumentParser(
        description="Switch between modes in A2C or loading models from previous games")
    parser.add_argument("--demo_mode", "-d", help="Renders the gym environment", action="store_true")
    parser.add_argument("--load_model", "-l", help="Loads the model of previously gained training data", action="store_true")
    parser.add_argument("--normalized_plot", "-n", help="Shows plot of normalized reward", action="store_true")
    parser.add_argument("--save_data", "-s", help="Saves data for algo_compare.py", action="store_true")
    global args
    args = parser.parse_args()
    return args


