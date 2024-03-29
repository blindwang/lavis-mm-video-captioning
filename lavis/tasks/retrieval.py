"""
 Copyright (c) 2022, salesforce.com, inc.
 All rights reserved.
 SPDX-License-Identifier: BSD-3-Clause
 For full license text, see the LICENSE file in the repo root or https://opensource.org/licenses/BSD-3-Clause
"""

import json
import logging
import os

import numpy as np
import torch
import wandb
from lavis.common.dist_utils import is_main_process
from lavis.common.registry import registry
from lavis.tasks.base_task import BaseTask


@registry.register_task("retrieval")
class RetrievalTask(BaseTask):
    def __init__(self, cfg):
        super().__init__()

        self.cfg = cfg

    @classmethod
    def setup_task(cls, cfg):
        run_cfg = cfg.run_cfg

        return cls(cfg=run_cfg)

    def train_step(self, model, samples):
        output = model(samples)
        # if hasattr(model, 'loss_config') and 'DAL' in model.loss_config:
        # if is_main_process():
        #     wandb.log({'loss_dal': output['loss_dal']})
        return output["loss"]

    def evaluation(self, model, data_loader, **kwargs):
        score_i2t, score_t2i = model.compute_sim_matrix(data_loader, task_cfg=self.cfg)

        if is_main_process():
            eval_result = self._report_metrics(
                score_i2t,
                score_t2i,
                data_loader.dataset.txt2img,
                data_loader.dataset.img2txt,
            )
            logging.info(eval_result)
        else:
            eval_result = None

        return eval_result

    def after_evaluation(self, val_result, **kwargs):
        return val_result

    @staticmethod
    @torch.no_grad()
    def _report_metrics(scores_i2t, scores_t2i, txt2img, img2txt):
        # Dual Softmax
        scores_i2t, scores_t2i = torch.tensor(scores_i2t), torch.tensor(scores_t2i)
        scores_i2t = torch.softmax(scores_i2t, dim=0) * scores_i2t
        scores_t2i = torch.softmax(scores_t2i, dim=0) * scores_t2i
        scores_i2t, scores_t2i = scores_i2t.numpy(), scores_t2i.numpy()

        # Images->Text
        ranks = np.zeros(scores_i2t.shape[0])
        for index, score in enumerate(scores_i2t):
            inds = np.argsort(score)[::-1]
            # Score
            rank = 1e20
            for i in img2txt[index]:
                tmp = np.where(inds == i)[0][0]
                if tmp < rank:
                    rank = tmp
            ranks[index] = rank

        # Compute metrics
        tr1 = 100.0 * len(np.where(ranks < 1)[0]) / len(ranks)
        tr5 = 100.0 * len(np.where(ranks < 5)[0]) / len(ranks)
        tr10 = 100.0 * len(np.where(ranks < 10)[0]) / len(ranks)
        t_median_r = np.median(ranks) + 1
        t_mean_r = np.mean(ranks) + 1
        i2t_ranks = ranks.copy()

        # Text->Images
        ranks = np.zeros(scores_t2i.shape[0])

        for index, score in enumerate(scores_t2i):
            inds = np.argsort(score)[::-1]
            ranks[index] = np.where(inds == txt2img[index])[0][0]

        # Compute metrics
        ir1 = 100.0 * len(np.where(ranks < 1)[0]) / len(ranks)
        ir5 = 100.0 * len(np.where(ranks < 5)[0]) / len(ranks)
        ir10 = 100.0 * len(np.where(ranks < 10)[0]) / len(ranks)
        i_median_r = np.median(ranks) + 1
        i_mean_r = np.mean(ranks) + 1

        tr_mean = (tr1 + tr5 + tr10) / 3
        ir_mean = (ir1 + ir5 + ir10) / 3
        r_mean = (tr_mean + ir_mean) / 2

        agg_metrics = (tr1 + tr5 + tr10) / 3

        eval_result = {
            "txt_r1": tr1,
            "txt_r5": tr5,
            "txt_r10": tr10,
            "txt_r_mean": tr_mean,
            "img_r1": ir1,
            "img_r5": ir5,
            "img_r10": ir10,
            "img_r_mean": ir_mean,
            "r_mean": r_mean,
            "agg_metrics": agg_metrics,
            "txt_medianR": t_median_r,
            "txt_meanR": t_mean_r,
            "img_medianR": i_median_r,
            "img_meanR": i_mean_r,
        }
        with open(
            os.path.join(registry.get_path("output_dir"), "evaluate.txt"), "a"
        ) as f:
            f.write(json.dumps(eval_result) + "\n")
        wandb.log(data=eval_result)
        np.savez(
            os.path.join(registry.get_path("output_dir"), "evaluate.npz"),
            i2t_ranks=i2t_ranks, i2t_sim=scores_i2t,
            t2i_ranks=ranks, t2i_sim=scores_t2i,
        )
        return eval_result
