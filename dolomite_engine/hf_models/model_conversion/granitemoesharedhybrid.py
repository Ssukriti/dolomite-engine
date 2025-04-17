import torch
from transformers import (
    AutoConfig,
    AutoTokenizer,
    GenerationConfig,
    GraniteMoeHybridConfig,
    GraniteMoeHybridForCausalLM,
)
from typing import List

from ...utils import SafeTensorsWeightsManager, download_repo
from ..modeling_utils import (
    get_attention_head_type,
    interleave_query_key_value_tensor_for_attention,
    split_query_key_value_tensor_for_attention,
)
from ..models import GPTDolomiteConfig


def export_to_huggingface_granitemoehybrid(pretrained_model_name_or_path: str, save_path: str) -> None:
    config: GPTDolomiteConfig = AutoConfig.from_pretrained(pretrained_model_name_or_path)
    original_config = _export_config_to_huggingface(config)

    safetensors_weights_manager = SafeTensorsWeightsManager(pretrained_model_name_or_path)
    state_dict = _export_state_dict_to_huggingface(
        safetensors_weights_manager,
        config.num_layers,
        seq_mixer_block_types = _get_sequence_mixer_block_types(config),
        num_heads = original_config.num_attention_heads,
        num_key_value_heads = original_config.num_key_value_heads,
        head_dim = original_config.hidden_size // original_config.num_attention_heads,
    )

    SafeTensorsWeightsManager.save_state_dict(state_dict, save_path)
    original_config.save_pretrained(save_path)

    original_generation_config = GenerationConfig.from_model_config(original_config)
    original_generation_config.save_pretrained(save_path)

    try:
        tokenizer = AutoTokenizer.from_pretrained(pretrained_model_name_or_path)
        tokenizer.save_pretrained(save_path, legacy_format=False)
    except:
        pass

def _get_sequence_mixer_block_types(config: GPTDolomiteConfig) -> List:
        blocks = getattr(config, "sequence_mixer_blocks")

        def _get(block, key):
            return block.get(key) if isinstance(block, dict) else getattr(block, key)

        seq_mixer_block_types = []
        for block in blocks:
            block_type = _get(block, "sequence_mixer_type")
            #block type mamba to use HybridMambaCache
            if block_type == "mamba2":
                block_type = "mamba"
            elif block_type == "softmax_attention":
                block_type = "attention"
            seq_mixer_block_types.append(block_type)
        return seq_mixer_block_types

def _export_config_to_huggingface(config: GPTDolomiteConfig) -> GraniteMoeHybridConfig:
    assert config.normalization_function == "rmsnorm"

    config.check_equal_for_all_and_get_value("sequence_mixer_blocks", "add_bias", False)
    config.check_equal_for_all_and_get_value("mlp_blocks", "add_bias", False)
    config.check_equal_for_all_and_get_value("mlp_blocks", "activation_function", "swiglu")
    config.check_equal_for_all_and_get_value("mlp_blocks", "mlp_type", "MoE")
    shared_intermediate_size = config.check_equal_for_all_and_get_value("mlp_blocks", "shared_intermediate_size")

    original_config = GraniteMoeHybridConfig(
        vocab_size=config.vocab_size,
        max_position_embeddings=config.max_position_embeddings,
        hidden_size=config.hidden_size,
        num_hidden_layers=config.num_layers,
        num_attention_heads=config.check_equal_for_all_seq_mixer_and_get_value(key="sequence_mixer_blocks", sequence_mixer_type="softmax_attention", key_block="num_attention_heads"),
        shared_intermediate_size=0 if shared_intermediate_size is None else shared_intermediate_size,
        num_key_value_heads=config.check_equal_for_all_seq_mixer_and_get_value(key="sequence_mixer_blocks", sequence_mixer_type="softmax_attention", key_block="num_key_value_heads"),
        intermediate_size=config.check_equal_for_all_and_get_value("mlp_blocks", "intermediate_size"),
        hidden_act="silu",
        rms_norm_eps=config.layer_norm_epsilon,
        use_cache=config.use_cache,
        attention_bias=config.check_equal_for_all_seq_mixer_and_get_value(key="sequence_mixer_blocks", sequence_mixer_type="softmax_attention", key_block="add_bias"),
        tie_word_embeddings=config.tie_word_embeddings,
        initializer_range=config.initializer_range,
        rope_theta=config.rope_theta,
        rope_scaling=config.rope_scaling,
        attention_dropout=config.check_equal_for_all_seq_mixer_and_get_value(key="sequence_mixer_blocks", sequence_mixer_type="softmax_attention", key_block="dropout"),
        num_local_experts=config.check_equal_for_all_and_get_value("mlp_blocks", "num_experts"),
        num_experts_per_tok=config.check_equal_for_all_and_get_value("mlp_blocks", "num_experts_per_tok"),
        router_aux_loss_coef=config.router_aux_loss_coef,
        bos_token_id=config.bos_token_id,
        eos_token_id=config.eos_token_id,
        pad_token_id=config.pad_token_id,
        embedding_multiplier=1 if config.m_emb is None else config.m_emb,
        residual_multiplier=1 if config.m_residual is None else config.m_residual,
        logits_scaling=1 if config.m_width is None else config.m_width,
        attention_multiplier=config.check_equal_for_all_seq_mixer_and_get_value(key="sequence_mixer_blocks", sequence_mixer_type="softmax_attention", key_block="attention_multiplier"),
        mamba_n_groups = config.check_equal_for_all_seq_mixer_and_get_value(key="sequence_mixer_blocks", sequence_mixer_type="mamba2", key_block="num_groups"),
        mamba_n_heads = config.check_equal_for_all_seq_mixer_and_get_value(key="sequence_mixer_blocks", sequence_mixer_type="mamba2", key_block="num_heads"),
        mamba_d_state = config.check_equal_for_all_seq_mixer_and_get_value(key="sequence_mixer_blocks", sequence_mixer_type="mamba2", key_block="state_size"),
        mamba_d_conv = config.check_equal_for_all_seq_mixer_and_get_value(key="sequence_mixer_blocks", sequence_mixer_type="mamba2", key_block="conv_kernel_size"),
        mamba_chunk_size = config.check_equal_for_all_seq_mixer_and_get_value(key="sequence_mixer_blocks", sequence_mixer_type="mamba2", key_block="chunk_size"),
        mamba_conv_bias = config.check_equal_for_all_seq_mixer_and_get_value(key="sequence_mixer_blocks", sequence_mixer_type="mamba2", key_block="use_conv_bias"),
        mamba_proj_bias = config.check_equal_for_all_seq_mixer_and_get_value(key="sequence_mixer_blocks", sequence_mixer_type="mamba2", key_block="add_bias"),
        layer_types = _get_sequence_mixer_block_types(config),
        normalization_function=config.normalization_function,
        position_embedding_type=config.position_embedding_type,
        init_method=config.init_method,
        architectures=[GraniteMoeHybridForCausalLM.__name__],

    )

    return original_config


def _export_state_dict_to_huggingface(
    safetensors_weights_manager: SafeTensorsWeightsManager,
    num_layers: int,
    seq_mixer_block_types: List,
    num_heads: int,
    num_key_value_heads: int,
    head_dim: int,
) -> None:
    state_dict = {
        "model.embed_tokens.weight": safetensors_weights_manager.get_tensor("transformer.wte.weight"),
        "model.norm.weight": safetensors_weights_manager.get_tensor("transformer.ln_f.weight"),
    }
    attention_head_type = get_attention_head_type(num_heads, num_key_value_heads)

    if safetensors_weights_manager.has_tensor("lm_head.weight"):
        state_dict["lm_head.weight"] = safetensors_weights_manager.get_tensor("lm_head.weight")

    for layer_idx in range(num_layers):
        state_dict[f"model.layers.{layer_idx}.input_layernorm.weight"] = safetensors_weights_manager.get_tensor(
            f"transformer.h.{layer_idx}.ln_1.weight"
        )
        state_dict[f"model.layers.{layer_idx}.post_attention_layernorm.weight"] = (
            safetensors_weights_manager.get_tensor(f"transformer.h.{layer_idx}.ln_2.weight")
        )

        state_dict[f"model.layers.{layer_idx}.block_sparse_moe.router.layer.weight"] = (
            safetensors_weights_manager.get_tensor(f"transformer.h.{layer_idx}.mlp_block.gate.weight")
        )

        state_dict[f"model.layers.{layer_idx}.block_sparse_moe.input_linear.weight"] = _split_and_reorder_for_glu(
            safetensors_weights_manager.get_tensor(f"transformer.h.{layer_idx}.mlp_block.c_fc.weight"), dim=1
        )
        state_dict[f"model.layers.{layer_idx}.block_sparse_moe.output_linear.weight"] = (
            safetensors_weights_manager.get_tensor(f"transformer.h.{layer_idx}.mlp_block.c_proj.weight")
        )

        if seq_mixer_block_types[layer_idx] == "mamba":
            # mamba weights
            state_dict[f"model.layers.{layer_idx}.mamba.conv1d.weight"] = (
                safetensors_weights_manager.get_tensor(f"transformer.h.{layer_idx}.sequence_mixer.conv1d.weight")
            )
            state_dict[f"model.layers.{layer_idx}.mamba.conv1d.bias"] = (
                safetensors_weights_manager.get_tensor(f"transformer.h.{layer_idx}.sequence_mixer.conv1d.bias")
            )
            state_dict[f"model.layers.{layer_idx}.mamba.in_proj.weight"] = (
                safetensors_weights_manager.get_tensor(f"transformer.h.{layer_idx}.sequence_mixer.in_proj.weight")
            )
            state_dict[f"model.layers.{layer_idx}.mamba.dt_bias"] = (
                safetensors_weights_manager.get_tensor(f"transformer.h.{layer_idx}.sequence_mixer.dt_bias")
            )
            state_dict[f"model.layers.{layer_idx}.mamba.A_log"] = (
                safetensors_weights_manager.get_tensor(f"transformer.h.{layer_idx}.sequence_mixer.A_log")
            )
            state_dict[f"model.layers.{layer_idx}.mamba.D"] = (
                safetensors_weights_manager.get_tensor(f"transformer.h.{layer_idx}.sequence_mixer.D")
            )
            state_dict[f"model.layers.{layer_idx}.mamba.out_proj.weight"] = (
                safetensors_weights_manager.get_tensor(f"transformer.h.{layer_idx}.sequence_mixer.out_proj.weight")
            )
            state_dict[f"model.layers.{layer_idx}.mamba.norm.weight"] = (
                safetensors_weights_manager.get_tensor(f"transformer.h.{layer_idx}.sequence_mixer.norm.weight")
            )
        elif seq_mixer_block_types[layer_idx] == "attention":
            query_weight, key_weight, value_weight = split_query_key_value_tensor_for_attention(
            safetensors_weights_manager.get_tensor(f"transformer.h.{layer_idx}.sequence_mixer.c_attn.weight"),
                num_heads,
                num_key_value_heads,
                head_dim,
                attention_head_type,
            )
            state_dict[f"model.layers.{layer_idx}.self_attn.q_proj.weight"] = query_weight
            state_dict[f"model.layers.{layer_idx}.self_attn.k_proj.weight"] = key_weight
            state_dict[f"model.layers.{layer_idx}.self_attn.v_proj.weight"] = value_weight

            state_dict[f"model.layers.{layer_idx}.self_attn.o_proj.weight"] = safetensors_weights_manager.get_tensor(
                f"transformer.h.{layer_idx}.sequence_mixer.c_proj.weight"
            )

        if safetensors_weights_manager.has_tensor(f"transformer.h.{layer_idx}.mlp_block.c_fc_shared.weight"):
            state_dict[f"model.layers.{layer_idx}.shared_mlp.input_linear.weight"] = _split_and_reorder_for_glu(
                safetensors_weights_manager.get_tensor(f"transformer.h.{layer_idx}.mlp_block.c_fc_shared.weight"),
                dim=0,
            )
            state_dict[f"model.layers.{layer_idx}.shared_mlp.output_linear.weight"] = (
                safetensors_weights_manager.get_tensor(f"transformer.h.{layer_idx}.mlp_block.c_proj_shared.weight")
            )

    return state_dict


def _split_and_reorder_for_glu(weight: torch.Tensor, dim: int) -> torch.Tensor:
    x, y = weight.chunk(2, dim=dim)
    weight = torch.cat([y, x], dim=dim)
    return weight
