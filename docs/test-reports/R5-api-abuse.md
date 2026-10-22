# API abuse suite · R5 · 22 Oct 2026 00:52 IST

Source: tests/test_api_abuse.py (gate step 5). Every row must pass.

| Check | Test | Result |
| --- | --- | --- |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[abc-/api/people]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[abc-/api/accounts]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[abc-/api/goals]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[abc-/api/blockers]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[abc-/api/money-blocked]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[abc-/api/scorecard]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[abc-/api/inbox]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[abc-/api/commitments]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[abc-/api/decisions]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[abc-/api/signals]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[abc-/api/events]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[abc-/api/suggest/warm]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[abc-/api/suggest/best]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[abc-/api/suggest/newco]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[abc-/api/suggest/res]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1.5-/api/people]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1.5-/api/accounts]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1.5-/api/goals]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1.5-/api/blockers]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1.5-/api/money-blocked]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1.5-/api/scorecard]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1.5-/api/inbox]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1.5-/api/commitments]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1.5-/api/decisions]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1.5-/api/signals]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1.5-/api/events]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1.5-/api/suggest/warm]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1.5-/api/suggest/best]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1.5-/api/suggest/newco]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1.5-/api/suggest/res]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[-/api/people]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[-/api/accounts]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[-/api/goals]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[-/api/blockers]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[-/api/money-blocked]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[-/api/scorecard]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[-/api/inbox]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[-/api/commitments]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[-/api/decisions]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[-/api/signals]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[-/api/events]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[-/api/suggest/warm]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[-/api/suggest/best]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[-/api/suggest/newco]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[-/api/suggest/res]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1e9-/api/people]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1e9-/api/accounts]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1e9-/api/goals]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1e9-/api/blockers]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1e9-/api/money-blocked]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1e9-/api/scorecard]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1e9-/api/inbox]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1e9-/api/commitments]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1e9-/api/decisions]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1e9-/api/signals]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1e9-/api/events]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1e9-/api/suggest/warm]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1e9-/api/suggest/best]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1e9-/api/suggest/newco]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[1e9-/api/suggest/res]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[0x10-/api/people]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[0x10-/api/accounts]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[0x10-/api/goals]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[0x10-/api/blockers]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[0x10-/api/money-blocked]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[0x10-/api/scorecard]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[0x10-/api/inbox]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[0x10-/api/commitments]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[0x10-/api/decisions]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[0x10-/api/signals]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[0x10-/api/events]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[0x10-/api/suggest/warm]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[0x10-/api/suggest/best]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[0x10-/api/suggest/newco]` | PASS |
| Wrong types in limit and offset are rejected with 422. | `test_wrong_types_are_422[0x10-/api/suggest/res]` | PASS |
| A limit above 1000 or an offset above 100000 is rejected. | `test_huge_limits_are_422[/api/people]` | PASS |
| A limit above 1000 or an offset above 100000 is rejected. | `test_huge_limits_are_422[/api/accounts]` | PASS |
| A limit above 1000 or an offset above 100000 is rejected. | `test_huge_limits_are_422[/api/goals]` | PASS |
| A limit above 1000 or an offset above 100000 is rejected. | `test_huge_limits_are_422[/api/blockers]` | PASS |
| A limit above 1000 or an offset above 100000 is rejected. | `test_huge_limits_are_422[/api/money-blocked]` | PASS |
| A limit above 1000 or an offset above 100000 is rejected. | `test_huge_limits_are_422[/api/scorecard]` | PASS |
| A limit above 1000 or an offset above 100000 is rejected. | `test_huge_limits_are_422[/api/inbox]` | PASS |
| A limit above 1000 or an offset above 100000 is rejected. | `test_huge_limits_are_422[/api/commitments]` | PASS |
| A limit above 1000 or an offset above 100000 is rejected. | `test_huge_limits_are_422[/api/decisions]` | PASS |
| A limit above 1000 or an offset above 100000 is rejected. | `test_huge_limits_are_422[/api/signals]` | PASS |
| A limit above 1000 or an offset above 100000 is rejected. | `test_huge_limits_are_422[/api/events]` | PASS |
| A limit above 1000 or an offset above 100000 is rejected. | `test_huge_limits_are_422[/api/suggest/warm]` | PASS |
| A limit above 1000 or an offset above 100000 is rejected. | `test_huge_limits_are_422[/api/suggest/best]` | PASS |
| A limit above 1000 or an offset above 100000 is rejected. | `test_huge_limits_are_422[/api/suggest/newco]` | PASS |
| A limit above 1000 or an offset above 100000 is rejected. | `test_huge_limits_are_422[/api/suggest/res]` | PASS |
| Negative offset and zero or negative limit are rejected. | `test_negative_offsets_are_422[/api/people]` | PASS |
| Negative offset and zero or negative limit are rejected. | `test_negative_offsets_are_422[/api/accounts]` | PASS |
| Negative offset and zero or negative limit are rejected. | `test_negative_offsets_are_422[/api/goals]` | PASS |
| Negative offset and zero or negative limit are rejected. | `test_negative_offsets_are_422[/api/blockers]` | PASS |
| Negative offset and zero or negative limit are rejected. | `test_negative_offsets_are_422[/api/money-blocked]` | PASS |
| Negative offset and zero or negative limit are rejected. | `test_negative_offsets_are_422[/api/scorecard]` | PASS |
| Negative offset and zero or negative limit are rejected. | `test_negative_offsets_are_422[/api/inbox]` | PASS |
| Negative offset and zero or negative limit are rejected. | `test_negative_offsets_are_422[/api/commitments]` | PASS |
| Negative offset and zero or negative limit are rejected. | `test_negative_offsets_are_422[/api/decisions]` | PASS |
| Negative offset and zero or negative limit are rejected. | `test_negative_offsets_are_422[/api/signals]` | PASS |
| Negative offset and zero or negative limit are rejected. | `test_negative_offsets_are_422[/api/events]` | PASS |
| Negative offset and zero or negative limit are rejected. | `test_negative_offsets_are_422[/api/suggest/warm]` | PASS |
| Negative offset and zero or negative limit are rejected. | `test_negative_offsets_are_422[/api/suggest/best]` | PASS |
| Negative offset and zero or negative limit are rejected. | `test_negative_offsets_are_422[/api/suggest/newco]` | PASS |
| Negative offset and zero or negative limit are rejected. | `test_negative_offsets_are_422[/api/suggest/res]` | PASS |
| SQL, wildcards and script text in search come back as data, never run. | `test_sql_and_script_text_is_just_data['; drop table contact; --]` | PASS |
| SQL, wildcards and script text in search come back as data, never run. | `test_sql_and_script_text_is_just_data[' or 1=1 --]` | PASS |
| SQL, wildcards and script text in search come back as data, never run. | `test_sql_and_script_text_is_just_data[%]` | PASS |
| SQL, wildcards and script text in search come back as data, never run. | `test_sql_and_script_text_is_just_data[_]` | PASS |
| SQL, wildcards and script text in search come back as data, never run. | `test_sql_and_script_text_is_just_data[\\]` | PASS |
| SQL, wildcards and script text in search come back as data, never run. | `test_sql_and_script_text_is_just_data[<script>alert(1)</script>]` | PASS |
| SQL, wildcards and script text in search come back as data, never run. | `test_sql_and_script_text_is_just_data[" onmouseover="alert(1)]` | PASS |
| SQL, wildcards and script text in search come back as data, never run. | `test_sql_and_script_text_is_just_data[../../etc/passwd]` | PASS |
| SQL, wildcards and script text in search come back as data, never run. | `test_sql_and_script_text_is_just_data[\xf1and\xfa \u540d\u524d \U0001f600]` | PASS |
| Search text over 100 characters is rejected, not cut silently. | `test_search_too_long_is_422` | PASS |
| Path traversal and odd ids never return a file or a row. | `test_path_traversal_is_refused[/static/..%2f..%2fpyproject.toml]` | PASS |
| Path traversal and odd ids never return a file or a row. | `test_path_traversal_is_refused[/static/%2e%2e/%2e%2e/pyproject.toml]` | PASS |
| Path traversal and odd ids never return a file or a row. | `test_path_traversal_is_refused[/static/../app.py]` | PASS |
| Path traversal and odd ids never return a file or a row. | `test_path_traversal_is_refused[/static/..\\..\\pyproject.toml]` | PASS |
| Path traversal and odd ids never return a file or a row. | `test_path_traversal_is_refused[/static/app/..%2f..%2f..%2fpyproject.toml]` | PASS |
| Path traversal and odd ids never return a file or a row. | `test_path_traversal_is_refused[/static/app/%2e%2e/%2e%2e/app.py]` | PASS |
| Path traversal and odd ids never return a file or a row. | `test_path_traversal_is_refused[/api/people/..%2F1]` | PASS |
| Path traversal and odd ids never return a file or a row. | `test_path_traversal_is_refused[/api/people/1%2F..%2F2]` | PASS |
| Path traversal and odd ids never return a file or a row. | `test_path_traversal_is_refused[/api/accounts/-1]` | PASS |
| Path traversal and odd ids never return a file or a row. | `test_path_traversal_is_refused[/api/people/99999999999999999999]` | PASS |
| Every endpoint rejects a parameter it does not declare. | `test_unknown_parameters_are_422` | PASS |
| A filter outside the closed list is rejected. | `test_unknown_filter_is_422` | PASS |
| 50 parallel requests all succeed: no 5xx, no pool exhaustion. | `test_fifty_parallel_requests` | PASS |
| A sync that commits mid-request does not change what that request reads. | `test_sheet_change_during_a_request` | PASS |
| A client that sends an old sync_id gets 409 and the current id. | `test_expired_sync_id_is_409` | PASS |
| No env value (sheet id, tokens, key path, DB URL) appears in any response or error. | `test_no_secrets_in_any_response` | PASS |
| An internal error returns a plain 500 with no trace or SQL. | `test_server_error_has_no_stack_trace` | PASS |
| POST, PUT, PATCH and DELETE are refused on every endpoint except the act routes (R2). | `test_methods_other_than_get_are_refused` | PASS |
| Security headers on API, page and error responses; no CORS header anywhere. | `test_security_headers_on_every_response` | PASS |
| Every act POST without the X-Saley-Act header is refused with 403. | `test_act_posts_need_the_header` | PASS |
| A cross-site or same-site page cannot call an act route, even with the header. | `test_act_posts_refuse_cross_site_calls` | PASS |
| An unknown action, a huge target list, odd fields or a bad id are rejected with 422. | `test_act_unknown_action_and_huge_lists_are_422` | PASS |
| GET, PUT, PATCH and DELETE on the act routes are refused; other POST paths are 404. | `test_act_routes_accept_only_post` | PASS |
| Every /api route, every method, answers 401 to a call with no session (R4, D114). | `test_every_api_route_refuses_a_call_without_a_session` | PASS |
| The X-Saley-As header works only with the mock sign-in on this machine (D50, D116). | `test_act_post_needs_a_signed_in_local_user` | PASS |
| Inbox, Commitments and Decisions refuse a filter value outside their closed list. | `test_team_view_filters_are_a_closed_list[/api/inbox?type=write]` | PASS |
| Inbox, Commitments and Decisions refuse a filter value outside their closed list. | `test_team_view_filters_are_a_closed_list[/api/inbox?type=]` | PASS |
| Inbox, Commitments and Decisions refuse a filter value outside their closed list. | `test_team_view_filters_are_a_closed_list[/api/commitments?state=overdue]` | PASS |
| Inbox, Commitments and Decisions refuse a filter value outside their closed list. | `test_team_view_filters_are_a_closed_list[/api/commitments?owner=SID]` | PASS |
| Inbox, Commitments and Decisions refuse a filter value outside their closed list. | `test_team_view_filters_are_a_closed_list[/api/commitments?owner=sid;drop]` | PASS |
| Inbox, Commitments and Decisions refuse a filter value outside their closed list. | `test_team_view_filters_are_a_closed_list[/api/decisions?surface=email]` | PASS |
| Inbox, Commitments and Decisions refuse a filter value outside their closed list. | `test_team_view_filters_are_a_closed_list[/api/decisions?undo=maybe]` | PASS |
| Inbox, Commitments and Decisions refuse a filter value outside their closed list. | `test_team_view_filters_are_a_closed_list[/api/decisions?state=deleted]` | PASS |
| Inbox, Commitments and Decisions refuse a filter value outside their closed list. | `test_team_view_filters_are_a_closed_list[/api/decisions?person=1%3Bdrop]` | PASS |
| Inbox, Commitments and Decisions refuse a filter value outside their closed list. | `test_team_view_filters_are_a_closed_list[/api/decisions?person=aaaaaaaaaaaaaaaaaaaaa]` | PASS |
| /api/pack and /api/scorecard return allowed fields only; bad week 422; no session 401. | `test_r4_pack_and_scorecard_contract` | PASS |
| With no pack built yet, /api/pack is 200 with `empty` and null pack fields (D229). | `test_r4_pack_with_no_pack_is_an_empty_answer` | PASS |
| The week picker moves only to a week with a pack: prev/next are stored Mondays (D229). | `test_pack_neighbours_point_only_at_stored_packs` | PASS |
| R5 T3: tasks_offset is a whole number from 0 to 1000. Anything else is refused. | `test_tasks_offset_bad_values_are_422[abc]` | PASS |
| R5 T3: tasks_offset is a whole number from 0 to 1000. Anything else is refused. | `test_tasks_offset_bad_values_are_422[1.5]` | PASS |
| R5 T3: tasks_offset is a whole number from 0 to 1000. Anything else is refused. | `test_tasks_offset_bad_values_are_422[]` | PASS |
| R5 T3: tasks_offset is a whole number from 0 to 1000. Anything else is refused. | `test_tasks_offset_bad_values_are_422[1e9]` | PASS |
| R5 T3: tasks_offset is a whole number from 0 to 1000. Anything else is refused. | `test_tasks_offset_bad_values_are_422[0x10]` | PASS |
| R5 T3: tasks_offset is a whole number from 0 to 1000. Anything else is refused. | `test_tasks_offset_bad_values_are_422[-1]` | PASS |
| R5 T3: tasks_offset is a whole number from 0 to 1000. Anything else is refused. | `test_tasks_offset_bad_values_are_422[1001]` | PASS |
| R5 T3: tasks_offset is a whole number from 0 to 1000. Anything else is refused. | `test_tasks_offset_bad_values_are_422[99999999999999999999]` | PASS |
| test_tasks_offset_edges_are_accepted[0] | `test_tasks_offset_edges_are_accepted[0]` | PASS |
| test_tasks_offset_edges_are_accepted[10] | `test_tasks_offset_edges_are_accepted[10]` | PASS |
| test_tasks_offset_edges_are_accepted[1000] | `test_tasks_offset_edges_are_accepted[1000]` | PASS |
| R5 T3 (D153): a column Done names a task; a value beside it, or a bad name, is 422. | `test_done_task_name_only_and_never_a_client_value` | PASS |
| T4 review (D154, D169): list, invisible, non-ASCII, CRLF, filled and pending cases. | `test_email_write_value_abuse` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[all-/api/signals]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[all-/api/events]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[all-/api/suggest/warm]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[all-/api/suggest/res]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[-/api/signals]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[-/api/events]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[-/api/suggest/warm]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[-/api/suggest/res]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[New-/api/signals]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[New-/api/events]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[New-/api/suggest/warm]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[New-/api/suggest/res]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[SEEN-/api/signals]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[SEEN-/api/events]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[SEEN-/api/suggest/warm]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[SEEN-/api/suggest/res]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[new;seen-/api/signals]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[new;seen-/api/events]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[new;seen-/api/suggest/warm]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[new;seen-/api/suggest/res]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[1-/api/signals]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[1-/api/events]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[1-/api/suggest/warm]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[1-/api/suggest/res]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[new%20-/api/signals]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[new%20-/api/events]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[new%20-/api/suggest/warm]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[new%20-/api/suggest/res]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[../x-/api/signals]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[../x-/api/events]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[../x-/api/suggest/warm]` | PASS |
| R5 T7: view takes new or seen only. Anything else is refused. | `test_view_outside_new_or_seen_is_422[../x-/api/suggest/res]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[abc-new-/api/signals]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[abc-new-/api/events]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[abc-new-/api/suggest/warm]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[abc-new-/api/suggest/res]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[abc-seen-/api/signals]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[abc-seen-/api/events]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[abc-seen-/api/suggest/warm]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[abc-seen-/api/suggest/res]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[1.5-new-/api/signals]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[1.5-new-/api/events]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[1.5-new-/api/suggest/warm]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[1.5-new-/api/suggest/res]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[1.5-seen-/api/signals]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[1.5-seen-/api/events]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[1.5-seen-/api/suggest/warm]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[1.5-seen-/api/suggest/res]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[-new-/api/signals]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[-new-/api/events]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[-new-/api/suggest/warm]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[-new-/api/suggest/res]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[-seen-/api/signals]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[-seen-/api/events]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[-seen-/api/suggest/warm]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[-seen-/api/suggest/res]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[-1-new-/api/signals]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[-1-new-/api/events]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[-1-new-/api/suggest/warm]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[-1-new-/api/suggest/res]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[-1-seen-/api/signals]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[-1-seen-/api/events]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[-1-seen-/api/suggest/warm]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[-1-seen-/api/suggest/res]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[1001-new-/api/signals]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[1001-new-/api/events]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[1001-new-/api/suggest/warm]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[1001-new-/api/suggest/res]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[1001-seen-/api/signals]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[1001-seen-/api/events]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[1001-seen-/api/suggest/warm]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[1001-seen-/api/suggest/res]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[99999999999999999999-new-/api/signals]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[99999999999999999999-new-/api/events]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[99999999999999999999-new-/api/suggest/warm]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[99999999999999999999-new-/api/suggest/res]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[99999999999999999999-seen-/api/signals]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[99999999999999999999-seen-/api/events]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[99999999999999999999-seen-/api/suggest/warm]` | PASS |
| R5 T7: in a tabbed list the offset is 0 to 1000. | `test_tab_offset_is_a_whole_number_from_0_to_1000[99999999999999999999-seen-/api/suggest/res]` | PASS |
| test_tab_offset_edges_are_accepted[0-/api/signals] | `test_tab_offset_edges_are_accepted[0-/api/signals]` | PASS |
| test_tab_offset_edges_are_accepted[0-/api/events] | `test_tab_offset_edges_are_accepted[0-/api/events]` | PASS |
| test_tab_offset_edges_are_accepted[0-/api/suggest/warm] | `test_tab_offset_edges_are_accepted[0-/api/suggest/warm]` | PASS |
| test_tab_offset_edges_are_accepted[0-/api/suggest/res] | `test_tab_offset_edges_are_accepted[0-/api/suggest/res]` | PASS |
| test_tab_offset_edges_are_accepted[10-/api/signals] | `test_tab_offset_edges_are_accepted[10-/api/signals]` | PASS |
| test_tab_offset_edges_are_accepted[10-/api/events] | `test_tab_offset_edges_are_accepted[10-/api/events]` | PASS |
| test_tab_offset_edges_are_accepted[10-/api/suggest/warm] | `test_tab_offset_edges_are_accepted[10-/api/suggest/warm]` | PASS |
| test_tab_offset_edges_are_accepted[10-/api/suggest/res] | `test_tab_offset_edges_are_accepted[10-/api/suggest/res]` | PASS |
| test_tab_offset_edges_are_accepted[1000-/api/signals] | `test_tab_offset_edges_are_accepted[1000-/api/signals]` | PASS |
| test_tab_offset_edges_are_accepted[1000-/api/events] | `test_tab_offset_edges_are_accepted[1000-/api/events]` | PASS |
| test_tab_offset_edges_are_accepted[1000-/api/suggest/warm] | `test_tab_offset_edges_are_accepted[1000-/api/suggest/warm]` | PASS |
| test_tab_offset_edges_are_accepted[1000-/api/suggest/res] | `test_tab_offset_edges_are_accepted[1000-/api/suggest/res]` | PASS |
| R5 T7 (D156): cat takes a topic name. A letter or any other text is refused. | `test_cat_letters_and_unknown_text_are_422[F]` | PASS |
| R5 T7 (D156): cat takes a topic name. A letter or any other text is refused. | `test_cat_letters_and_unknown_text_are_422[A]` | PASS |
| R5 T7 (D156): cat takes a topic name. A letter or any other text is refused. | `test_cat_letters_and_unknown_text_are_422[L]` | PASS |
| R5 T7 (D156): cat takes a topic name. A letter or any other text is refused. | `test_cat_letters_and_unknown_text_are_422[Z]` | PASS |
| R5 T7 (D156): cat takes a topic name. A letter or any other text is refused. | `test_cat_letters_and_unknown_text_are_422[]` | PASS |
| R5 T7 (D156): cat takes a topic name. A letter or any other text is refused. | `test_cat_letters_and_unknown_text_are_422[Hiring;]` | PASS |
| R5 T7 (D156): cat takes a topic name. A letter or any other text is refused. | `test_cat_letters_and_unknown_text_are_422[1 or 1=1]` | PASS |
| R5 T7 (D156): cat takes a topic name. A letter or any other text is refused. | `test_cat_letters_and_unknown_text_are_422[Fund]` | PASS |
| R5 T7 (D156): cat takes a topic name. A letter or any other text is refused. | `test_cat_letters_and_unknown_text_are_422[Product  launch]` | PASS |
| R5 T7 (D156): cat takes a topic name. A letter or any other text is refused. | `test_cat_letters_and_unknown_text_are_422[xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx]` | PASS |
| R5 T7 (D156): cat takes a topic name. A letter or any other text is refused. | `test_cat_letters_and_unknown_text_are_422[%00]` | PASS |
| test_cat_names_are_accepted_in_any_case[Hiring] | `test_cat_names_are_accepted_in_any_case[Hiring]` | PASS |
| test_cat_names_are_accepted_in_any_case[Funding] | `test_cat_names_are_accepted_in_any_case[Funding]` | PASS |
| test_cat_names_are_accepted_in_any_case[Product launch] | `test_cat_names_are_accepted_in_any_case[Product launch]` | PASS |
| test_cat_names_are_accepted_in_any_case[Research] | `test_cat_names_are_accepted_in_any_case[Research]` | PASS |
| test_cat_names_are_accepted_in_any_case[Partnership] | `test_cat_names_are_accepted_in_any_case[Partnership]` | PASS |
| test_cat_names_are_accepted_in_any_case[Event] | `test_cat_names_are_accepted_in_any_case[Event]` | PASS |
| test_cat_names_are_accepted_in_any_case[Other] | `test_cat_names_are_accepted_in_any_case[Other]` | PASS |
| test_cat_names_are_accepted_in_any_case[hiring] | `test_cat_names_are_accepted_in_any_case[hiring]` | PASS |
| test_cat_names_are_accepted_in_any_case[PRODUCT LAUNCH] | `test_cat_names_are_accepted_in_any_case[PRODUCT LAUNCH]` | PASS |
| R5-X04: the R5 params and routes. Allowed fields only, 422 for bad input, 401 unsigned. | `test_r5_contract` | PASS |

**279 of 279 passed.**
