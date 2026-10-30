# Added to the current user (sign-in, validate_token, profile) for Durian OS "View as"
# (shared View as spec 6). The person's API token outlives the View as and would not be
# marked with the admin, so it is not shown while viewing.
view_as = durian_os_view_as
json.impersonated_by view_as&.impersonated_by
json.view_as_ends_at view_as&.ends_at&.iso8601
json.access_token nil if view_as
