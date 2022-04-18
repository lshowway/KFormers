import math
import os.path

import torch.nn as nn
import torch

from parameters import parse_args

args = parse_args()

if args.backbone_model_type == 'roberta':
    from transformers.models.roberta.modeling_roberta import \
        (
        RobertaEmbeddings as BackboneEmbeddings,
        RobertaLayer as BackboneLayer,
        RobertaPreTrainedModel as BackbonePreTrainedModel,
        RobertaPooler as BackbonePooler,
        RobertaModel as BackboneModel,
    )  # 这个是Roberta
elif args.backbone_model_type == 'bert':
    from transformers.models.bert.modeling_bert import \
        (
        BertEmbeddings as BackboneEmbeddings,
        BertLayer as BackboneLayer,
        BertPreTrainedModel as BackbonePreTrainedModel,
        BertPooler as BackbonePooler,
    )  # Bert
else:
    pass

from transformers.models.distilbert.modeling_distilbert import \
    (
    Embeddings as KEmbeddings,
    TransformerBlock as KnowledgeLayer,
    DistilBertPreTrainedModel
)  # 这个是k module



class GNN(nn.Module):
    def __init__(self, config, config_k=None):
        super(GNN, self).__init__()
        self.config_k = config_k
        if config_k is not None:
            self.projection = nn.Linear(config_k.hidden_size, config.hidden_size)

            self.num_attention_heads = config.num_attention_heads
            self.attention_head_size = int(config.hidden_size / config.num_attention_heads)
            self.all_head_size = self.num_attention_heads * self.attention_head_size

            self.query = nn.Linear(config.hidden_size, self.all_head_size)
            self.key = nn.Linear(config.hidden_size, self.all_head_size)
            self.value = nn.Linear(config.hidden_size, self.all_head_size)

            self.LayerNorm = nn.LayerNorm(config.hidden_size, eps=config.layer_norm_eps)
            self.dropout = nn.Dropout(config.attention_probs_dropout_prob)

            self.map = nn.Linear(100, config.hidden_size)

    def transpose_for_scores(self, x):
        new_x_shape = x.size()[:-1] + (self.num_attention_heads, self.attention_head_size)
        x = x.view(*new_x_shape)
        return x.permute(0, 2, 1, 3)

    def forward(self, hidden_states, k_hidden_states=None, entity_embed=None):
        if self.config_k is not None:
            # 处理original input text的表征
            # 第一种： 使用CLS的表征
            # center_states = hidden_states[:, :1, :]  # batch, 1, d=1024
            # 第二种：全部token的表征都使用
            center_states = hidden_states  # batch, L1, d1
            L1 = hidden_states.size(1)

            # 处理N条description的表征
            # 第一种使用CLS的表征
            # knowledge_states = k_hidden_states  # # batch, N, L2, d2=768
            batch, neighbour_num, description_len, d2 = k_hidden_states.size()
            knowledge_states = k_hidden_states[:, :, 0, :]  # batch, N, d2=768
            knowledge_states = self.projection(knowledge_states)  # batch, N, d1=1024

            # 将original input text的表征和description的表征合起来
            entity_embed  = self.map(entity_embed)
            entity_embed = self.dropout(self.LayerNorm(entity_embed))
            center_knowledge_states = torch.cat([center_states, knowledge_states, entity_embed], dim=1)  # batch, L1+N+1, d1

            # 这个attention_mask是center和neighbour之间是否可见，也可以不加，默认就是互相可见
            # attention_mask = torch.ones(batch, L).unsqueeze(1).unsqueeze(1).to(hidden_states.device)  # batch, 1, 1, L1+K

            # query = self.query(center_knowledge_states[:, :1])  # batch, 1, d
            query = self.query(center_knowledge_states)  # batch, L1+N, d1
            key = self.key(center_knowledge_states)  # batch, L1+N, d1
            value = self.value(center_knowledge_states)  # batch, L1+N, d1

            query = self.transpose_for_scores(query)  # batch, d3, L1+K, d4
            key = self.transpose_for_scores(key)   # batch, d3, L1+K, d4
            value = self.transpose_for_scores(value)  # batch, d3, L1+K, d4

            attention_scores = torch.matmul(query, key.transpose(-1, -2))  # batch, d3, 1, L1+N
            attention_scores = attention_scores / math.sqrt(self.attention_head_size)
            # if attention_mask is not None:
            #     attention_scores = attention_scores + attention_mask  # batch, d3, 1, L1+N
            attention_probs = nn.Softmax(dim=-1)(attention_scores)  # batch, d3, 1, L1+N

            attention_probs = self.dropout(attention_probs)
            context_layer = torch.matmul(attention_probs, value)  # batch, d3, L1+N, d4

            context_layer = context_layer.permute(0, 2, 1, 3).contiguous()
            new_context_layer_shape = context_layer.size()[:-2] + (self.all_head_size,)
            context_layer = context_layer.view(*new_context_layer_shape)  # batch, L1+N, d1(d3*d4)

            # 使用受description影响后的original text的表征,为啥是-1
            # hidden_states[:, -1, :] = context_layer[:, 0, :]  # 替换掉 batch L1, d1
            hidden_states[:, 0, :] = context_layer[:, 0, :]  # 替换掉 batch L1, d1
            # hidden_states[:, :L1, :] = context_layer[:, :L1, :]

            return hidden_states  # batch, d
        else:
            return hidden_states



class KFormersLayer(nn.Module):
    # gnn可以整合到一起这个模块，也可以把gnn拿出来，现在是拿出来，因为还没写
    def __init__(self, config, config_k=None):
        super(KFormersLayer, self).__init__()
        if config_k is not None:
            self.backbone_layer = BackboneLayer(config)  # 处理qk pair的backbone module，分类
            self.k_layer = KnowledgeLayer(config_k)  # 处理description的knowledge module，表示
        else:
            self.backbone_layer = BackboneLayer(config)  # 处理qk pair的backbone module，分类
            self.k_layer = None
        self.gnn = GNN(config, config_k)

    def forward(self, hidden_states, attention_mask,
                k_hidden_states_list=None, k_attention_mask_list=None,
                entity_embed=None):

        layer_outputs = self.backbone_layer(hidden_states=hidden_states, attention_mask=attention_mask)
        hidden_states = layer_outputs[0]  # batch L d
        if self.k_layer is not None and k_hidden_states_list is not None:  # 现在先测试baseline没问题，之后用上面一行
            k_layer_outputs = self.k_layer(x=k_hidden_states_list, attn_mask=k_attention_mask_list)
            k_layer_outputs = k_layer_outputs[0]
            batch, neighbour_num, description_len = k_attention_mask_list.size()
            k_layer_outputs = k_layer_outputs.reshape(batch, neighbour_num, description_len, -1)  # batch, N, L2, d2
            hidden_states = self.gnn(hidden_states, k_layer_outputs, entity_embed)  # 这里对batch 0 d进行处理，使用neighbour的cls位？
            return hidden_states, k_layer_outputs.reshape(batch*neighbour_num, description_len, -1)
        else:
            return hidden_states, k_hidden_states_list



class KFormersEncoder(nn.Module):
    # knowledge module, small model, two tower, representation model
    def __init__(self, config, config_k, backbone_knowledge_dict):
        super(KFormersEncoder, self).__init__()
        self.num_hidden_layers = config.num_hidden_layers
        module_list = []
        for i in range(config.num_hidden_layers):
            if i in backbone_knowledge_dict:
                module_list.append(KFormersLayer(config=config, config_k=config_k))
            else:
                module_list.append(KFormersLayer(config=config, config_k=None))
        self.layer = nn.ModuleList(module_list)

    def forward(self, hidden_states, attention_mask=None,
                k_hidden_states_list=None, k_attention_mask_list=None,
                entity_embed=None):

        for i, layer_module in enumerate(self.layer):
            layer_outputs = layer_module(hidden_states=hidden_states, attention_mask=attention_mask,
                                         k_hidden_states_list=k_hidden_states_list,
                                         k_attention_mask_list=k_attention_mask_list,
                                         entity_embed=entity_embed)
            hidden_states = layer_outputs[0]
            k_hidden_states_list = layer_outputs[1]

        outputs = (hidden_states, k_hidden_states_list)
        return outputs



class KFormersModel(nn.Module):
    def __init__(self, config, config_k, backbone_knowledge_dict):
        super(KFormersModel, self).__init__()
        self.config = config
        self.config_k = config_k
        self.embeddings = BackboneEmbeddings(config)
        self.k_embeddings = KEmbeddings(config_k)

        # self.entity_embedding_table = nn.Embedding(1267331+1, 100)
        # self.entity_embedding_table.weight.requires_grad = False

        t = load_entity_embedding(config_k.post_trained_checkpoint_embedding)
        self.entity_embedding_table = torch.nn.Embedding.from_pretrained(t, freeze=True)

        self.encoder = KFormersEncoder(config, config_k, backbone_knowledge_dict)

        if not config.add_pooling_layer:
            self.pooler = BackbonePooler(config)
        else:
            self.pooler = None

    def get_extended_attention_mask(self, attention_mask, input_shape):
        if attention_mask.dim() == 3:
            extended_attention_mask = attention_mask[:, None, :, :]
        elif attention_mask.dim() == 2:
            extended_attention_mask = attention_mask[:, None, None, :]
        else:
            raise ValueError("Wrong shape for input_ids (shape {}) or attention_mask (shape {})".format(
                    input_shape, attention_mask.shape))
        extended_attention_mask = extended_attention_mask.to(dtype=attention_mask.dtype)  # fp16 compatibility
        extended_attention_mask = (1.0 - extended_attention_mask) * -10000.0
        return extended_attention_mask

    def forward(self, input_ids=None, attention_mask=None, token_type_ids=None, position_ids=None,
                k_input_ids_list=None, k_attention_mask_list=None, k_token_type_ids_list=None, k_position_ids=None, entities=None):

        extended_attention_mask = self.get_extended_attention_mask(attention_mask, input_shape=input_ids.size())
        embedding_output = self.embeddings(input_ids=input_ids, position_ids=position_ids,
                                           token_type_ids=token_type_ids)

        if k_input_ids_list is not None:
            batch, neighbour_num, description_len = k_input_ids_list.size()
            k_embedding_output = self.k_embeddings(input_ids=k_input_ids_list.reshape(-1, description_len))  # distilBert没有position和segment
            entity_embed = self.entity_embedding_table(entities)
        else:
            k_embedding_output = None
            entity_embed=None
        encoder_outputs = self.encoder(hidden_states=embedding_output, attention_mask=extended_attention_mask,
                                       k_hidden_states_list=k_embedding_output,
                                       k_attention_mask_list=k_attention_mask_list,
                                       entity_embed=entity_embed)  # batch L d
        original_text_output, description_output = encoder_outputs
        if self.pooler is None:
            return encoder_outputs  # original_text_output, description_output
        else:
            pooled_output = self.pooler(original_text_output)
            return (original_text_output, pooled_output, description_output)



# --------------------------------------------------------------------------
class RobertaOutputLayerEntityTyping(nn.Module):
    """Head for entity-level classification tasks."""

    def __init__(self, config):
        super().__init__()
        self.dense = nn.Linear(config.in_hidden_size, config.hidden_size)
        self.dropout = nn.Dropout(config.hidden_dropout_prob)
        self.out_proj = nn.Linear(config.hidden_size, config.num_labels)

    def forward(self, features, **kwargs):
        # x = features[:, 0, :]  # take <s> token (equiv. to [CLS])
        x = features  # of entity
        x = self.dropout(x)
        x = self.dense(x)
        x = torch.tanh(x)
        x = self.dropout(x)
        x = self.out_proj(x)
        return x



class RobertaOutputLayerSequenceClassification(nn.Module):
    """Head for sentence-level classification tasks."""

    def __init__(self, config):
        super().__init__()
        self.dense = nn.Linear(config.hidden_size, config.hidden_size)
        self.dropout = nn.Dropout(config.hidden_dropout_prob)
        self.out_proj = nn.Linear(config.hidden_size, config.num_labels)

    def forward(self, features, **kwargs):
        x = features[:, 0, :]  # take <s> token (equiv. to [CLS])
        x = self.dropout(x)
        x = self.dense(x)
        x = torch.tanh(x)
        x = self.dropout(x)
        x = self.out_proj(x)
        return x



class RobertaOutputLayerRelationClassification(nn.Module):
    """Head for two-entity classification tasks."""

    def __init__(self, config):
        super().__init__()
        self.dense = nn.Linear(config.hidden_size * 2, config.hidden_size)
        self.dropout = nn.Dropout(config.hidden_dropout_prob)
        self.out_proj = nn.Linear(config.hidden_size, config.num_labels)

    def forward(self, features, **kwargs):
        # x = features[:, 0, :]  # take <s> token (equiv. to [CLS])
        x = features  # of entity
        x = self.dropout(x)
        x = self.dense(x)
        x = torch.tanh(x)
        x = self.dropout(x)
        x = self.out_proj(x)
        return x



# ------------------------------------------------------------------------
class RobertaForEntityTyping(BackbonePreTrainedModel):  # 这个不能继承一个类吧？两个？
    def __init__(self, config):
        super(RobertaForEntityTyping, self).__init__(config)
        self.num_labels = config.num_labels
        config.add_pooling_layer = False

        self.roberta = BackboneModel(config)
        config.in_hidden_size = config.hidden_size
        self.classifier = RobertaOutputLayerEntityTyping(config)

        self.loss = nn.BCEWithLogitsLoss(reduction='mean')
        self.init_weights()

    def forward(self, input_ids=None, attention_mask=None, token_type_ids=None, position_ids=None, \
                k_input_ids_list=None, k_mask=None, k_attention_mask_list=None,
                k_token_type_ids_list=None, k_position_ids=None, labels=None, start_id=None, entities=None):

        outputs = self.roberta(input_ids=input_ids, attention_mask=attention_mask,
                                token_type_ids=token_type_ids, position_ids=position_ids)
        original_text_output = outputs[0]  # batch L d

        start_id = start_id.unsqueeze(1)  # batch 1 L
        entity_vec = torch.bmm(start_id, original_text_output).squeeze(1)  # batch d

        # entity_vec = original_text_output[:, 0, :] + entity_vec_2

        logits = self.classifier(entity_vec)
        if labels is not None:
            loss = self.loss(logits.view(-1, self.num_labels), labels.view(-1, self.num_labels))
            return logits, loss
        else:
            return logits



class RobertaForRelationClassification(BackbonePreTrainedModel):  # 这个不能继承一个类吧？两个？
    def __init__(self, config):
        super(RobertaForRelationClassification, self).__init__(config)
        self.num_labels = config.num_labels
        config.add_pooling_layer = False

        self.roberta = BackboneModel(config)
        self.classifier = RobertaOutputLayerRelationClassification(config)

        self.loss = nn.CrossEntropyLoss(reduction='mean')
        self.init_weights()

    def forward(self, input_ids=None, attention_mask=None, token_type_ids=None, position_ids=None, \
                k_input_ids_list=None, k_mask=None, k_attention_mask_list=None,
                k_token_type_ids_list=None, k_position_ids=None, labels=None, start_id=None, entities=None):

        outputs = self.roberta(input_ids=input_ids, attention_mask=attention_mask,
                                token_type_ids=token_type_ids, position_ids=position_ids)
        original_text_output = outputs[0]  # batch L d

        if len(start_id.shape) == 3:
            sub_start_id, obj_start_id = start_id.split(1, dim=1) # split to 2, each is 1
            subj_output = torch.bmm(sub_start_id, original_text_output)
            obj_output = torch.bmm(obj_start_id, original_text_output)
            entity_vec = torch.cat([subj_output.squeeze(1), obj_output.squeeze(1)], dim=1)
            logits = self.classifier(entity_vec)

            if labels is not None:
                loss = self.loss(logits.view(-1, self.num_labels), labels.view(-1).to(torch.long))
                return logits, loss
            else:
                return logits
        else:
            raise ValueError("the entity index is wrong")



class RobertaForSequenceClassification(BackbonePreTrainedModel):

    def __init__(self, config):
        super(RobertaForSequenceClassification, self).__init__(config)
        self.num_labels = config.num_labels
        config.add_pooling_layer = False

        self.roberta = BackboneModel(config)
        self.classifier = RobertaOutputLayerSequenceClassification(config)

        self.init_weights()

    def forward(self, input_ids=None, attention_mask=None, token_type_ids=None, position_ids=None,
                k_input_ids_list=None, k_mask=None, k_attention_mask_list=None,
                k_token_type_ids_list=None, k_position_ids=None, labels=None, start_id=None):

        outputs = self.roberta(input_ids, attention_mask=attention_mask,
            token_type_ids=token_type_ids, position_ids=position_ids)
        sequence_output = outputs[0]
        logits = self.classifier(sequence_output)

        if labels is not None:
            if self.num_labels == 1:
                #  We are doing regression
                loss_fct = nn.MSELoss()
                loss = loss_fct(logits.view(-1), labels.view(-1))
            else:
                loss_fct = nn.CrossEntropyLoss(reduction='mean')
                loss = loss_fct(logits.view(-1, self.num_labels), labels.view(-1))

            return logits, loss
        else:
            return logits



# ---------------------------------------------------------------------
class KFormersForEntityTyping(BackbonePreTrainedModel):
    def __init__(self, config, config_k, backbone_knowledge_dict):
        super(KFormersForEntityTyping, self).__init__(config)
        self.num_labels = config.num_labels
        config.add_pooling_layer = False

        self.kformers = KFormersModel(config, config_k, backbone_knowledge_dict)
        config.in_hidden_size = config.hidden_size
        self.classifier = RobertaOutputLayerEntityTyping(config)

        self.loss = nn.BCEWithLogitsLoss(reduction='mean')
        self.init_weights()

    def forward(self, input_ids=None, attention_mask=None, token_type_ids=None, position_ids=None, \
                k_input_ids_list=None, k_mask=None, k_attention_mask_list=None,
                k_token_type_ids_list=None, k_position_ids=None, labels=None, start_id=None, entities=None):

        outputs = self.kformers(input_ids=input_ids, attention_mask=attention_mask,
                                token_type_ids=token_type_ids, position_ids=position_ids,
                                k_input_ids_list=k_input_ids_list, k_attention_mask_list=k_attention_mask_list,
                                k_token_type_ids_list=k_token_type_ids_list, k_position_ids=k_position_ids, entities=entities)

        original_text_output = outputs[0]  # batch L d
        start_id = start_id.unsqueeze(1)  # batch 1 L
        entity_vec = torch.bmm(start_id, original_text_output).squeeze(1)  # batch d

        logits = self.classifier(entity_vec)
        if labels is not None:
            loss = self.loss(logits.view(-1, self.num_labels), labels.view(-1, self.num_labels))
            return logits, loss
        else:
            return logits



class KFormersForRelationClassification(BackbonePreTrainedModel):  # 这个不能继承一个类吧？两个？
    def __init__(self, config, config_k, backbone_knowledge_dict):
        super(KFormersForRelationClassification, self).__init__(config)
        self.num_labels = config.num_labels
        config.add_pooling_layer = False

        self.kformers = KFormersModel(config, config_k, backbone_knowledge_dict)
        self.classifier = RobertaOutputLayerRelationClassification(config)

        self.loss = nn.CrossEntropyLoss(reduction='mean')
        self.init_weights()

    def forward(self, input_ids=None, attention_mask=None, token_type_ids=None, position_ids=None, \
                k_input_ids_list=None, k_mask=None, k_attention_mask_list=None,
                k_token_type_ids_list=None, k_position_ids=None, labels=None, start_id=None, entities=None):
        outputs = self.kformers(input_ids=input_ids, attention_mask=attention_mask,
                                token_type_ids=token_type_ids, position_ids=position_ids,
                                k_input_ids_list=k_input_ids_list, k_attention_mask_list=k_attention_mask_list,
                                k_token_type_ids_list=k_token_type_ids_list, k_position_ids=k_position_ids, entities=entities)
        original_text_output = outputs[0]  # batch L d

        if len(start_id.shape) == 3:
            sub_start_id, obj_start_id = start_id.split(1, dim=1)  # split to 2, each is 1
            subj_output = torch.bmm(sub_start_id, original_text_output)
            obj_output = torch.bmm(obj_start_id, original_text_output)
            entity_vec = torch.cat([subj_output.squeeze(1), obj_output.squeeze(1)], dim=1)
            logits = self.classifier(entity_vec)

            if labels is not None:
                loss = self.loss(logits.view(-1, self.num_labels), labels.view(-1).to(torch.long))
                return logits, loss
            else:
                return logits
        else:
            raise ValueError("the entity index is wrong")



class KFormersForSequenceClassification(BackbonePreTrainedModel):

    def __init__(self, config, config_k, backbone_knowledge_dict):
        super(KFormersForSequenceClassification, self).__init__(config)
        self.num_labels = config.num_labels
        config.add_pooling_layer = False

        self.kformers = KFormersModel(config, config_k, backbone_knowledge_dict)
        self.classifier = RobertaOutputLayerSequenceClassification(config)

        self.init_weights()

    def forward(self, input_ids=None, attention_mask=None, token_type_ids=None, position_ids=None,
                k_input_ids_list=None, k_mask=None, k_attention_mask_list=None,
                k_token_type_ids_list=None, k_position_ids=None, labels=None, start_id=None):

        outputs = self.kformers(input_ids, attention_mask=attention_mask,
                                token_type_ids=token_type_ids, position_ids=position_ids,
                               k_input_ids_list=k_input_ids_list, k_attention_mask_list=k_attention_mask_list,
                               k_token_type_ids_list=k_token_type_ids_list, k_position_ids=k_position_ids)

        sequence_output = outputs[0]
        logits = self.classifier(sequence_output)

        if labels is not None:
            if self.num_labels == 1:
                #  We are doing regression
                loss_fct = nn.MSELoss()
                loss = loss_fct(logits.view(-1), labels.view(-1))
            else:
                loss_fct = nn.CrossEntropyLoss(reduction='mean')
                loss = loss_fct(logits.view(-1, self.num_labels), labels.view(-1))

            return logits, loss
        else:
            return logits


def load_entity_embedding(ckpt):
    entity_embedding_table = None
    # ckpt = "../phase2_pretrain_KFormers/output_update_6_40_60-90/checkpoint-260000/pytorch_model.bin"

    state_dict = torch.load(os.path.join(ckpt, 'pytorch_model.bin'))
    for k, v in state_dict.items():
        if 'entity_embeddings.entity_embeddings.weight' in k:
            entity_embedding_table = v
    t = torch.mean(entity_embedding_table, dim=0, keepdim=True)#, device=entity_embedding_table.device)
    return torch.cat([entity_embedding_table, t], dim=0)