# Phase 5B 强制测试矩阵映射

> 本文件把执行包中的 74 个矩阵 ID 映射到实际 pytest 断言。一个测试可以覆盖多个 ID；参数化测试的每个参数实例均由 pytest 独立执行。

## 统计

- 矩阵 ID：74 项。
- Phase 5B 专项门禁当前收集 62 个 pytest 用例，覆盖全部 74 项。
- RG05 另由 MCP 回归执行；RG07/RG08 同时由 Phase 4B/5A 回归再次验证。

## PS — 策略解析

| ID | pytest 断言来源 |
|---|---|
| PS01 | `pytests/test_plugin_bot_profile_scope.py::test_ps01_public_inherits_globally_enabled_plugin` |
| PS02 | `pytests/test_plugin_bot_profile_scope.py::test_ps02_ps03_group_inherits_allow_and_child_deny` |
| PS03 | `pytests/test_plugin_bot_profile_scope.py::test_ps02_ps03_group_inherits_allow_and_child_deny` |
| PS04 | `pytests/test_plugin_bot_profile_scope.py::test_ps04_ps05_isolated_group_requires_explicit_allow` |
| PS05 | `pytests/test_plugin_bot_profile_scope.py::test_ps04_ps05_isolated_group_requires_explicit_allow` |
| PS06 | `pytests/test_plugin_bot_profile_scope.py::test_ps06_ps07_kami_defaults_deny_and_explicit_allow` |
| PS07 | `pytests/test_plugin_bot_profile_scope.py::test_ps06_ps07_kami_defaults_deny_and_explicit_allow` |
| PS08 | `pytests/test_plugin_bot_profile_scope.py::test_ps08_global_disabled_cannot_be_restored` |
| PS09 | `pytests/test_plugin_bot_profile_scope.py::test_ps09_plugin_deny_precedes_tool_allow` |
| PS10 | `pytests/test_plugin_bot_profile_scope.py::test_ps10_policy_writes_increment_revision` |

## TL — Tool 与 Action

| ID | pytest 断言来源 |
|---|---|
| TL01 | `pytests/plugin_runtime/test_plugin_action_command_scope.py::test_tl01_tl02_denied_tool_is_hidden_and_direct_invoke_has_zero_rpc` |
| TL02 | `pytests/plugin_runtime/test_plugin_action_command_scope.py::test_tl01_tl02_denied_tool_is_hidden_and_direct_invoke_has_zero_rpc` |
| TL03 | `pytests/plugin_runtime/test_plugin_action_command_scope.py::test_tl03_cached_tool_call_is_rejected_after_policy_change` |
| TL04 | `pytests/plugin_runtime/test_plugin_action_command_scope.py::test_tl04_short_tool_rule_does_not_match_full_component` |
| TL05 | `pytests/plugin_runtime/test_plugin_action_command_scope.py::test_tl05_tool_filter_happens_before_duplicate_short_name_and_rpc_gate` |
| TL06 | `pytests/plugin_runtime/test_plugin_action_command_scope.py::test_tl06_tl07_legacy_action_requires_plugin_and_tool_allow` |
| TL07 | `pytests/plugin_runtime/test_plugin_action_command_scope.py::test_tl06_tl07_legacy_action_requires_plugin_and_tool_allow` |
| TL08 | `pytests/plugin_runtime/test_plugin_action_command_scope.py::test_tl08_cached_action_executor_rechecks_current_profile` |
| TL09 | `pytests/plugin_runtime/test_plugin_action_command_scope.py::test_tl09_concurrent_profiles_do_not_share_tool_visibility` |
| TL10 | `pytests/plugin_runtime/test_plugin_action_command_scope.py::test_tl10_kami_force_all_memory_does_not_open_plugins_or_tools` |

## CM — Command

| ID | pytest 断言来源 |
|---|---|
| CM01 | `pytests/plugin_runtime/test_plugin_action_command_scope.py::test_cm01_cm02_denied_command_is_not_candidate` |
| CM02 | `pytests/plugin_runtime/test_plugin_action_command_scope.py::test_cm01_cm02_denied_command_is_not_candidate` |
| CM03 | `pytests/plugin_runtime/test_plugin_action_command_scope.py::test_cm03_cm05_allowed_duplicate_command_keeps_execution_semantics` |
| CM04 | `pytests/plugin_runtime/test_plugin_action_command_scope.py::test_cm04_cached_command_executor_rechecks_policy_before_rpc` |
| CM05 | `pytests/plugin_runtime/test_plugin_action_command_scope.py::test_cm03_cm05_allowed_duplicate_command_keeps_execution_semantics` |
| CM06 | `pytests/plugin_runtime/test_plugin_action_command_scope.py::test_cm06_cm07_kami_only_sees_explicitly_allowed_command` |
| CM07 | `pytests/plugin_runtime/test_plugin_action_command_scope.py::test_cm06_cm07_kami_only_sees_explicitly_allowed_command`；`pytests/test_bot_route_command.py::test_kami_command_requires_exact_real_user_text`；`pytests/test_bot_route_command.py::test_kami_command_is_consumed_before_message_registration` |
| CM08 | `pytests/plugin_runtime/test_plugin_action_command_scope.py::test_cm08_cached_command_uses_bound_profile_not_forged_args` |

## EV — Event

| ID | pytest 断言来源 |
|---|---|
| EV01 | `pytests/plugin_runtime/test_plugin_event_hook_scope.py::test_ev01_ev02_ev03_denied_handlers_have_no_control_or_task` |
| EV02 | `pytests/plugin_runtime/test_plugin_event_hook_scope.py::test_ev01_ev02_ev03_denied_handlers_have_no_control_or_task` |
| EV03 | `pytests/plugin_runtime/test_plugin_event_hook_scope.py::test_ev01_ev02_ev03_denied_handlers_have_no_control_or_task` |
| EV04 | `pytests/plugin_runtime/test_plugin_event_hook_scope.py::test_ev04_ev05_allowed_nonblocking_captures_scope` |
| EV05 | `pytests/plugin_runtime/test_plugin_event_hook_scope.py::test_ev04_ev05_allowed_nonblocking_captures_scope` |
| EV06 | `pytests/plugin_runtime/test_plugin_event_hook_scope.py::test_ev06_hk06_unscoped_lifecycle_dispatch_uses_global_policy` |
| EV07 | `pytests/plugin_runtime/test_plugin_event_hook_scope.py::test_ev07_security_fields_are_removed_from_event_update` |

## HK — Hook

| ID | pytest 断言来源 |
|---|---|
| HK01 | `pytests/plugin_runtime/test_plugin_event_hook_scope.py::test_hk01_hk02_hk03_denied_hooks_cannot_mutate_abort_or_schedule` |
| HK02 | `pytests/plugin_runtime/test_plugin_event_hook_scope.py::test_hk01_hk02_hk03_denied_hooks_cannot_mutate_abort_or_schedule` |
| HK03 | `pytests/plugin_runtime/test_plugin_event_hook_scope.py::test_hk01_hk02_hk03_denied_hooks_cannot_mutate_abort_or_schedule` |
| HK04 | `pytests/plugin_runtime/test_plugin_event_hook_scope.py::test_hk04_hk05_allowed_hooks_keep_scope_and_modify` |
| HK05 | `pytests/plugin_runtime/test_plugin_event_hook_scope.py::test_hk04_hk05_allowed_hooks_keep_scope_and_modify` |
| HK06 | `pytests/plugin_runtime/test_plugin_event_hook_scope.py::test_ev06_hk06_unscoped_lifecycle_dispatch_uses_global_policy`；`pytests/test_bot_request_context.py::test_background_task_starts_without_request_context` |
| HK07 | `pytests/plugin_runtime/test_plugin_event_hook_scope.py::test_hk07_forged_scope_and_token_are_removed_from_modified_kwargs` |

## CF — 配置覆盖

| ID | pytest 断言来源 |
|---|---|
| CF01 | `pytests/plugin_runtime/test_plugin_config_overlay.py::test_cf01_cf11_request_and_legacy_overrides_are_applied_on_copy` |
| CF02 | `pytests/plugin_runtime/test_plugin_config_overlay.py::test_cf02_cf03_cf09_cf10_profile_overrides_are_isolated` |
| CF03 | `pytests/plugin_runtime/test_plugin_config_overlay.py::test_cf02_cf03_cf09_cf10_profile_overrides_are_isolated` |
| CF04 | `pytests/plugin_runtime/test_plugin_config_overlay.py::test_cf04_cf05_cf06_cf07_cf08_invalid_overrides_fail_closed` |
| CF05 | `pytests/plugin_runtime/test_plugin_config_overlay.py::test_cf04_cf05_cf06_cf07_cf08_invalid_overrides_fail_closed` |
| CF06 | `pytests/plugin_runtime/test_plugin_config_overlay.py::test_cf04_cf05_cf06_cf07_cf08_invalid_overrides_fail_closed` |
| CF07 | `pytests/plugin_runtime/test_plugin_config_overlay.py::test_cf04_cf05_cf06_cf07_cf08_invalid_overrides_fail_closed` |
| CF08 | `pytests/plugin_runtime/test_plugin_config_overlay.py::test_cf04_cf05_cf06_cf07_cf08_invalid_overrides_fail_closed` |
| CF09 | `pytests/plugin_runtime/test_plugin_config_overlay.py::test_cf02_cf03_cf09_cf10_profile_overrides_are_isolated` |
| CF10 | `pytests/plugin_runtime/test_plugin_config_overlay.py::test_cf02_cf03_cf09_cf10_profile_overrides_are_isolated` |
| CF11 | `pytests/plugin_runtime/test_plugin_config_overlay.py::test_cf01_cf11_request_and_legacy_overrides_are_applied_on_copy` |
| CF12 | `pytests/plugin_runtime/test_plugin_config_overlay.py::test_cf12_cf13_concurrent_effective_configs_do_not_share_mutable_state` |
| CF13 | `pytests/plugin_runtime/test_plugin_config_overlay.py::test_cf12_cf13_concurrent_effective_configs_do_not_share_mutable_state` |
| CF14 | `pytests/plugin_runtime/test_plugin_config_overlay.py::test_cf14_old_handler_signature_is_not_given_new_kwargs` |

## IV — Invocation token 与副作用

| ID | pytest 断言来源 |
|---|---|
| IV01 | `pytests/plugin_runtime/test_plugin_invocation_scope.py::test_iv01_iv02_iv03_iv04_capability_token_identity_and_revocation` |
| IV02 | `pytests/plugin_runtime/test_plugin_invocation_scope.py::test_iv01_iv02_iv03_iv04_capability_token_identity_and_revocation`；`pytests/plugin_runtime/test_plugin_invocation_scope.py::test_runner_overwrites_forged_capability_token_with_bound_token` |
| IV03 | `pytests/plugin_runtime/test_plugin_invocation_scope.py::test_iv01_iv02_iv03_iv04_capability_token_identity_and_revocation` |
| IV04 | `pytests/plugin_runtime/test_plugin_invocation_scope.py::test_iv01_iv02_iv03_iv04_capability_token_identity_and_revocation` |
| IV05 | `pytests/plugin_runtime/test_plugin_invocation_scope.py::test_iv05_supervisor_revokes_token_on_success_and_error`；`pytests/plugin_runtime/test_plugin_invocation_scope.py::test_iv05_supervisor_revokes_token_on_cancellation` |
| IV06 | `pytests/plugin_runtime/test_plugin_invocation_scope.py::test_iv06_denied_plugin_never_sends_rpc_or_issues_token` |
| IV07 | `pytests/plugin_runtime/test_plugin_invocation_scope.py::test_iv07_request_triggered_auto_reply_runs_only_for_allowed_profile` |
| IV08 | `pytests/plugin_runtime/test_plugin_invocation_scope.py::test_iv08_policy_change_blocks_send_database_and_memory_side_effects` |
| IV09 | `pytests/plugin_runtime/test_plugin_invocation_scope.py::test_iv09_unscoped_timer_uses_existing_global_capability_policy` |
| IV10 | `pytests/plugin_runtime/test_plugin_invocation_scope.py::test_iv10_token_value_is_absent_from_diagnostics_and_errors` |

## RG — 回归

| ID | pytest 断言来源 |
|---|---|
| RG01 | `pytests/plugin_runtime/test_plugin_action_command_scope.py::test_rg01_rg02_component_registry_chat_scope_and_allowed_session_remain_hard_gates` |
| RG02 | `pytests/plugin_runtime/test_plugin_action_command_scope.py::test_rg01_rg02_component_registry_chat_scope_and_allowed_session_remain_hard_gates` |
| RG03 | `pytests/plugin_runtime/test_plugin_scope_regressions.py::test_rg03_circuit_breaker_timeout_and_shutdown_gates_remain_active` |
| RG04 | `pytests/plugin_runtime/test_plugin_scope_regressions.py::test_rg04_builtin_tool_still_obeys_current_bot_profile_policy`；`pytests/test_bot_profile_tool_context.py::test_tool_registry_uses_current_bot_profile_full_component_policy` |
| RG05 | `pytests/mcp/test_mcp_service.py::test_rg05_service_reuses_unchanged_manager_and_preserves_context` |
| RG06 | `pytests/plugin_runtime/test_plugin_scope_regressions.py::test_rg06_message_gateway_does_not_restart_or_switch_accounts_per_profile`；`pytests/plugin_runtime/test_plugin_scope_regressions.py::test_rg06_gateway_supervisor_ignores_bound_profile_policy` |
| RG07 | `pytests/test_memory_access_resolver_phase4b.py::test_all_normal_never_includes_kami`；`pytests/test_memory_access_resolver_phase4b.py::test_kami_requires_manager_capabilities_and_writes_only_kami_space`；`pytests/test_kami_service.py::test_memory_access_audit_stores_hash_not_body` |
| RG08 | `pytests/plugin_runtime/test_plugin_scope_regressions.py::test_rg08_schema_remains_v46_without_phase5b_migration`；`pytests/test_database_migration_v45_to_v46.py::test_v45_to_v46_is_idempotent_and_backfills_legacy_messages` |

## 门禁命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  pytests/test_plugin_bot_profile_scope.py `
  pytests/plugin_runtime/test_plugin_event_hook_scope.py `
  pytests/plugin_runtime/test_plugin_config_overlay.py `
  pytests/plugin_runtime/test_plugin_invocation_scope.py `
  pytests/plugin_runtime/test_plugin_action_command_scope.py `
  pytests/plugin_runtime/test_plugin_scope_regressions.py `
  pytests/test_bot_profile_tool_context.py `
  pytests/test_bot_route_command.py
```
