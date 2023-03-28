"""nusacrowd zero-shot prompt.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1Ru8DyS2ALWfRdkjOPHj-KNjw6Pfa44Nd
"""
import os, sys
import csv
from os.path import exists

from numpy import argmax
import pandas as pd
from tqdm import tqdm
from sklearn.metrics import f1_score, accuracy_score
from nlu_prompt import get_prompt

import torch
import torch.nn.functional as F

from transformers import AutoTokenizer, AutoModelForSeq2SeqLM, AutoModelForCausalLM
from nusacrowd import NusantaraConfigHelper

#!pip install git+https://github.com/IndoNLP/nusa-crowd.git@release_exp
#!pip install transformers
#!pip install sentencepiece

DEBUG=False

"""# Loading NLU Datasets"""
TEXT_CLASSIFICATION_TASKS = [
    # Monolongual Senti, Emot, NLI 
    'emot_nusantara_text',
    'imdb_jv_nusantara_text',
    'indolem_sentiment_nusantara_text',
    'smsa_nusantara_text',    
    'indonli_nusantara_pairs',
    'su_emot_nusantara_text',
    
    # NusaX Sentiment
    'nusax_senti_ace_nusantara_text',
    'nusax_senti_ban_nusantara_text',
    'nusax_senti_bjn_nusantara_text',
    'nusax_senti_bug_nusantara_text',
    'nusax_senti_eng_nusantara_text',
    'nusax_senti_ind_nusantara_text',
    'nusax_senti_jav_nusantara_text',
    'nusax_senti_min_nusantara_text',
    'nusax_senti_sun_nusantara_text',
    
#     # Nusa Kalimat Emot
#     'nusa_kalimat_emot_sun_nusantara_text',
#     'nusa_kalimat_emot_jav_nusantara_text',
#     'nusa_kalimat_emot_min_nusantara_text',
    
#     # Nusa Kalimat Senti
#     'nusa_kalimat_senti_sun_nusantara_text',
#     'nusa_kalimat_senti_jav_nusantara_text',
#     'nusa_kalimat_senti_min_nusantara_text',
    
#     # Nusa Alinea Emot
#     'nusa_alinea_emot_sun_nusantara_text',
#     'nusa_alinea_emot_jav_nusantara_text',
#     'nusa_alinea_emot_min_nusantara_text',
#     'nusa_alinea_emot_bug_nusantara_text',
    
#     # Nusa Alinea Topic
#     'nusa_alinea_topic_sun_nusantara_text',
#     'nusa_alinea_topic_jav_nusantara_text',
#     'nusa_alinea_topic_min_nusantara_text',
#     'nusa_alinea_topic_bug_nusantara_text',
    
#     # Nusa Alinea Paragraph
#     'nusa_alinea_paragraph_sun_nusantara_text',
#     'nusa_alinea_paragraph_jav_nusantara_text',
#     'nusa_alinea_paragraph_min_nusantara_text',
#     'nusa_alinea_paragraph_bug_nusantara_text',
]

def to_prompt(input, prompt, labels, prompt_lang):
    # single label
    if 'text' in input:
        prompt = prompt.replace('[INPUT]', input['text'])
    else:
        prompt = prompt.replace('[INPUT_A]', input['text_1'])
        prompt = prompt.replace('[INPUT_B]', input['text_2'])

    # replace [OPTIONS] to A, B, or C
    if "[OPTIONS]" in prompt:
        new_labels = [f'"{l}"' for l in labels]
        new_labels[-1] = ("or " if 'EN' in prompt_lang else  "atau ") + new_labels[-1] 
        if len(new_labels) > 2:
            prompt = prompt.replace('[OPTIONS]', ', '.join(new_labels))
        else:
            prompt = prompt.replace('[OPTIONS]', ' '.join(new_labels))

    return prompt

def load_nlu_tasks():
    conhelps = NusantaraConfigHelper()
    nlu_datasets = {
        helper.config.name: helper.load_dataset() for helper in conhelps.filtered(lambda x: x.config.name in TEXT_CLASSIFICATION_TASKS)
    }
    return nlu_datasets

@torch.no_grad()
def get_logprobs(model, tokenizer, prompt, label_ids=None, label_attn=None):
    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=1024).to('cuda')
    input_ids, output_ids = inputs["input_ids"], inputs["input_ids"][:, 1:]
    
    outputs = model(**inputs, labels=input_ids)
    logits = outputs.logits
    
    if model.config.is_encoder_decoder:
        logprobs = torch.gather(F.log_softmax(logits, dim=2), 2, label_ids.unsqueeze(2)) * label_attn.unsqueeze(2)
        return logprobs.sum() / label_attn.sum()
    else:
        logprobs = torch.gather(F.log_softmax(logits, dim=2), 2, output_ids.unsqueeze(2))
        return logprobs.mean()

def predict_classification(model, tokenizer, prompt, labels):
    if model.config.is_encoder_decoder:
        labels_encoded = tokenizer(labels, add_special_tokens=False, padding=True, return_tensors='pt')
        list_label_ids =labels_encoded['input_ids'].to('cuda')
        list_label_attn =labels_encoded['attention_mask'].to('cuda')
        probs = [
                    get_logprobs(model, tokenizer, prompt.replace('[LABELS_CHOICE]', ''), label_ids.view(1,-1), label_attn.view(1,-1)) 
                     for (label_ids, label_attn) in zip(list_label_ids, list_label_attn)
                ]
    else:
        probs = [get_logprobs(model, tokenizer, prompt.replace('[LABELS_CHOICE]', label)) for label in labels]
    return probs

if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise ValueError('main_nlu_prompt.py <prompt_lang> <model_path_or_name>')

    prompt_lang = sys.argv[1]
    MODEL = sys.argv[2]

    os.makedirs('./outputs', exist_ok=True) 

    # Load Prompt
    DATA_TO_PROMPT = get_prompt(prompt_lang)

    # Load Dataset
    print('Load NLU Datasets...')
    nlu_datasets = load_nlu_tasks()

    print(f'Loaded {len(nlu_datasets)} NLU datasets')
    for i, dset_subset in enumerate(nlu_datasets.keys()):
        print(f'{i} {dset_subset}')

    # Load Model
    tokenizer = AutoTokenizer.from_pretrained(MODEL, truncation_side='left')
    if "bloom" in MODEL or "xglm" in MODEL or "gpt2" in MODEL:
        model = AutoModelForCausalLM.from_pretrained(MODEL).to('cuda')
    else:
        model = AutoModelForSeq2SeqLM.from_pretrained(MODEL).to('cuda')
        tokenizer.pad_token = tokenizer.eos_token # Use EOS to pad label
        
    model.eval()
    torch.no_grad()

    metrics = {}
    labels = []
    for i, dset_subset in enumerate(nlu_datasets.keys()):
        print(f'{i} {dset_subset}')
        if dset_subset not in DATA_TO_PROMPT or DATA_TO_PROMPT[dset_subset] is None:
            print('SKIP')
            continue

        if 'test' in nlu_datasets[dset_subset]:
            data = nlu_datasets[dset_subset]['test']
        else:
            data = nlu_datasets[dset_subset]['train']

        if DEBUG:
            print(dset_subset)

        label_names = data.features['label'].names
        # normalize some labels for more natural prompt:
        if dset_subset == 'imdb_jv_nusantara_text':
            label_names = ['positive', 'negative']
        if dset_subset == 'indonli_nusantara_pairs':
            label_names = ['no', 'yes', 'maybe']

        en_id_label_map = {
            '0': '0', '1': '1', '2': '2', '3': '3', '4': '4', '5': '5',	'special': 'khusus', 'general': 'umum',
            'no': 'tidak', 'yes': 'ya', 'maybe': 'mungkin', 'negative': 'negatif', 'positive': 'positif', 
            'east': 'timur', 'standard': 'standar', 'ngapak': 'ngapak', 'unknown': 'unknown',
            'neutral': 'netral', 'love': 'cinta', 'fear': 'takut', 'happy': 'senang', 'sad': 'sedih',
            'sadness': 'sedih', 'disgust': 'jijik', 'anger': 'marah', 'surprise': 'terkejut', 'joy': 'senang',
            'reject': 'ditolak', 'tax': 'pajak', 'partial': 'sebagian', 'others': 'lain-lain',
            'granted': 'dikabulkan', 'fulfill': 'penuh', 'correction': 'koreksi',
            'not abusive': 'tidak abusive', 'abusive': 'abusive', 'abusive and offensive': 'abusive dan offensive',
            'support': 'mendukung', 'against': 'bertentangan', 
        }
        
        # preprocess label (lower case & translate)
        label_names = [str(label).lower().replace("_"," ") for label in label_names]
        labels += label_names
        
        if 'ID' in prompt_lang:
            label_names = list(map(lambda lab: en_id_label_map[lab], label_names))

        # sample prompt
        print("LABEL NAME = ")
        print(label_names)
        print("SAMPLE PROMPT = ")
        print(to_prompt(data[0], DATA_TO_PROMPT[dset_subset], label_names, prompt_lang))
        print("\n")

        inputs = []
        preds = []
        golds = []
        
        # zero-shot inference
        if not exists(f'outputs/{dset_subset}_{prompt_lang}_{MODEL.split("/")[-1]}.csv'):
            with torch.inference_mode():
                for sample in tqdm(data):
                    prompt_text = to_prompt(sample, DATA_TO_PROMPT[dset_subset], label_names, prompt_lang)
                    out = predict_classification(model, tokenizer, prompt_text, label_names)
                    pred = argmax([o.cpu().detach() for o in out])
                    inputs.append(prompt_text)
                    preds.append(pred)
                    golds.append(sample['label'])
     
            inference_df = pd.DataFrame(list(zip(inputs, preds, golds)), columns =["Input", 'Pred', 'Gold'])
            inference_df.to_csv(f'outputs/{dset_subset}_{prompt_lang}_{MODEL.split("/")[-1]}.csv', index=False)
        # if output log exists, skip
        else:
            print("Output exist, use existing log instead")
            with open(f'outputs/{dset_subset}_{prompt_lang}_{MODEL.split("/")[-1]}.csv') as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    inputs.append(row["Input"])
                    preds.append(row["Pred"])
                    golds.append(row["Gold"])

        acc, f1 = accuracy_score(golds, preds), f1_score(golds, preds, average='macro')
        print(dset_subset)
        print('accuracy', acc)
        print('f1 macro', f1)
        metrics[dset_subset] = {'accuracy': acc, 'f1_score': f1}
        print("===\n\n")

    pd.DataFrame.from_dict(metrics).T.reset_index().to_csv(f'metrics/nlu_results_{prompt_lang}_{MODEL.split("/")[-1]}.csv', index=False)