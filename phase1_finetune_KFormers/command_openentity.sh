for len in 32
do
	for ent in 4
	do
		for des in 1
		do
for alpha in 1.0 
do
	for beta in  0.001
	do
for lr in 3e-5 2e-5
do
for seed in 12 42 57 1234 3407
do
python run_KFormers.py   --task_name  openentity  --fp16 True  --model_type  KFormers  --backbone_model_type luke  --knowledge_model_type  distilbert  --backbone_model_name_or_path  roberta-large  --knowledge_model_name_or_path  distilbert-base-uncased   --backbone_seq_length 256 --knowledge_seq_length $len   --qid_file  ../data/knowledge/pretrain/wikipedia_description/wikidata5m_des.wikipedia  --max_ent_num $ent  --max_des_num $des  --train_batch_size 4  --gradient_accumulation_steps 1 --valid_batch_size 256   --max_steps -1     --update_K_module False  --data_dir ../data/knowledge/OpenEntity  --output_dir PM_13   --epochs 3  --eval_steps 50  --learning_rate $lr  --warmup_steps -1  --alpha  $alpha  --beta $beta   --seed $seed  > logs_luke_openentity/len=$len.lr=$lr.ent=$ent.des=$des.alpha=$alpha.beta=$beta.seed=$seed
done
done
done
done
done
done
done


for len in 32
do
	for alpha in 1.0 
	do
		        for beta in 0.001 
				        do
						for lr in 1e-5 
						do
							for seed in 12 42 57 1234 3407
							do python run_KFormers.py  --task_name  tacred   --fp16 True   --model_type  KFormers  --backbone_model_type roberta  --knowledge_model_type  distilbert  --backbone_model_name_or_path  roberta-large  --knowledge_model_name_or_path  distilbert-base-uncased  --backbone_seq_length  256  --knowledge_seq_length $len  --qid_file  ../data/knowledge/pretrain/wikipedia_description/wikidata5m_des.wikipedia   --max_ent_num 4  --max_des_num 1  --train_batch_size 32   --valid_batch_size 256   --gradient_accumulation_steps 1   --max_steps -1   --update_K_module False   --data_dir ../data/knowledge/tacred  --output_dir FR_15   --epochs 5   --learning_rate $lr  --eval_steps 500  --warmup_steps -1    --alpha $alpha  --beta $beta  --seed $seed  > logs_luke_tacred/len=$len.lr=$lr.alpha=$alpha.beta=$beta.seed=$seed
							done
						done
					done
				done
			done



for len in 32
do
for alpha in 1.0 
do
        for beta in 0.001 
        do
for lr in 1e-5 2e-5
do
for seed in 12 42 57 1234 3407
do python run_KFormers.py  --task_name  fewrel   --fp16 True  --model_type  KFormers  --backbone_model_type luke  --knowledge_model_type  distilbert  --backbone_model_name_or_path  roberta-large  --knowledge_model_name_or_path  distilbert-base-uncased  --backbone_seq_length 256   --knowledge_seq_length $len   --qid_file  ../data/knowledge/pretrain/wikipedia_description/wikidata5m_des.wikipedia   --max_ent_num 4  --max_des_num 1  --train_batch_size 32   --valid_batch_size 256   --gradient_accumulation_steps 1   --max_steps -1   --update_K_module False   --data_dir ../data/knowledge/fewrel  --output_dir FR_14  --epochs 10  --eval_steps 200  --warmup_steps -1  --learning_rate $lr    --alpha $alpha  --beta $beta  --seed $seed  > logs_luke_fewrel/len=$len.lr=$lr.alpha=$alpha.beta=$beta.seed=$seed
done
done
done
done
done




python occupation.py
