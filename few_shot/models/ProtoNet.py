import torch
import torch.nn.functional as F
from sklearn.metrics import confusion_matrix
from tqdm import tqdm
import numpy as np
from .modules.ProtoNet import prototype_distance
from .backbones import get_backbone, count_parameters
import os

class ProtoNet(torch.nn.Module):
    def __init__(self, exp_dict):
        super().__init__()
        self.backbone = get_backbone(exp_dict, classify=False)
        self.backbone.cuda()

        if exp_dict['optimizer'] == 'sgd':
            self.optimizer = torch.optim.SGD(self.backbone.parameters(),
                                                lr=exp_dict['lr'],
                                                weight_decay=5e-4,
                                                momentum=0.9,
                                                nesterov=True)
        elif exp_dict['optimizer'] == 'adam':
            self.optimizer = torch.optim.Adam(self.backbone.parameters(),
                                                lr=exp_dict['lr'])

        self.scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(self.optimizer,
                                                                    mode='min',
                                                                    factor=0.1,
                                                                    patience=exp_dict['patience'],
                                                                    verbose=True)
        count_parameters(self.backbone)
        self.best_val = 0
        
    def train_on_loader(self, loader):
        _loss = 0
        _accuracy = 0
        _total = 0

        # self.temp += 1
        self.backbone.train()
        for episode in tqdm(loader):

            ## Boilerplate
            episode = episode[0] # undo collate
            # plot_episode(episode, classes_first=False, epoch=self.temp)
            support_set = episode["support_set"].cuda(non_blocking=False)
            query_set = episode["query_set"].cuda(non_blocking=False)

            ss, nclasses, c, h, w = support_set.size()
            qs, nclasses, c, h, w = query_set.size()

            absolute_labels = episode["targets"]
            relative_labels = absolute_labels.clone()
            
            # TODO: use episode['targets']
            support_relative_labels = torch.arange(episode['nclasses']).view(1, -1).repeat(episode['support_size'], 1).cuda().view(-1)
            query_relative_labels = torch.arange(episode['nclasses']).view(1, -1).repeat(episode['query_size'], 1).cuda().view(-1)

            ## Training
            self.optimizer.zero_grad()
            
            support_embeddings = self.backbone(support_set.view(ss * nclasses, c, h, w)).view(ss * nclasses, -1)
            query_embeddings = self.backbone(query_set.view(qs * nclasses, c, h, w)).view(qs*nclasses, -1)
            
            logits = prototype_distance(support_embeddings, query_embeddings, support_relative_labels)
            loss = F.cross_entropy(logits, query_relative_labels.long())
            _loss += float(loss)
            _total += 1
            loss.backward()
            self.optimizer.step()

            # Accuracy reporting 
            preds = logits.max(-1)[1]
            _accuracy += float((preds == query_relative_labels).float().mean())
        
        return {"train_loss": float(_loss) / _total,
                "train_accuracy": 100*float(_accuracy) / _total}

    @torch.no_grad()
    def val_on_loader(self, loader, mode='val', savedir=None):
        _accuracy = 0
        _total = 0
        _loss = 0
        _logits = []
        _targets = []
        self.backbone.eval()
        for episode in tqdm(loader):
            
            ## Boilerplate
            episode = episode[0] # undo collate
            support_set = episode["support_set"].cuda(non_blocking=False)
            query_set = episode["query_set"].cuda(non_blocking=False)

            ss, nclasses, c, h, w = support_set.size()
            qs, nclasses, c, h, w = query_set.size()

            if ss != episode["support_size"] or qs != episode["query_size"]:
                raise(RuntimeError("The dataset is too small for the current support and query sizes"))

            absolute_labels = episode["targets"]
            relative_labels = absolute_labels.clone()
            
            # TODO: use episode['targets']
            support_relative_labels = torch.arange(episode['nclasses']).view(1, -1).repeat(episode['support_size'], 1).cuda().view(-1)
            query_relative_labels = torch.arange(episode['nclasses']).view(1, -1).repeat(episode['query_size'], 1).cuda().view(-1)

            ## Testing
            support_embeddings = self.backbone(support_set.view(ss * nclasses, c, h, w)).view(ss * nclasses, -1)
            query_embeddings = self.backbone(query_set.view(qs * nclasses, c, h, w)).view(qs*nclasses, -1)
            
            logits = prototype_distance(support_embeddings, query_embeddings, support_relative_labels)
            loss = F.cross_entropy(logits, query_relative_labels.long())
            preds = logits.max(-1)[1]
            _loss += float(loss) * qs * nclasses
            _accuracy += float((preds == query_relative_labels).float().sum())
            _total += qs * nclasses
        
        self.scheduler.step(_loss / _total)
        
        return {"{}_loss".format(mode): _loss / _total, 
                "{}_accuracy".format(mode): 100*(_accuracy / _total)}

#TODO: move all this elsewhere:


    def get_state_dict(self):
        state = {}
        state["model"] = self.backbone.state_dict()
        state["optimizer"] = self.optimizer.state_dict()
        state["scheduler"] = self.scheduler.state_dict()
        return state

    def set_state_dict(self, state_dict):
        self.backbone.load_state_dict(state_dict["model"])
        self.optimizer.load_state_dict(state_dict["optimizer"])
        self.scheduler.load_state_dict(state_dict["scheduler"])

def plot_episode(episode, classes_first=True, savedir='figures/', epoch=0):
    import pylab
    sample_set = episode["support_set"].cpu()
    query_set = episode["query_set"].cpu()
    support_size = episode["support_size"]
    query_size = episode["query_size"]
    if not classes_first:
        sample_set = sample_set.permute(1, 0, 2, 3, 4)
        query_set = query_set.permute(1, 0, 2, 3, 4)
    n, support_size, c, h, w = sample_set.size()
    n, query_size, c, h, w = query_set.size()
    sample_set = ((sample_set / 2 + 0.5) * 255).numpy().astype('uint8').transpose((0, 3, 1, 4, 2)).reshape((n *h, support_size * w, c))
    pylab.imsave(os.path.join(savedir, 'support_set_{}.png'.format(epoch)), sample_set)
    query_set = ((query_set / 2 + 0.5) * 255).numpy().astype('uint8').transpose((0, 3, 1, 4, 2)).reshape((n *h, query_size * w, c))
    pylab.imsave(os.path.join(savedir, 'query_set_{}.png'.format(epoch)), query_set)
