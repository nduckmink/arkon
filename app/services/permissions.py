"""
Permission constants for Arkon's custom roles system.

All permission strings are defined here as the single source of truth.
Used by: require_permission() in auth_service, Role CRUD validation, and the frontend UI.
"""

ALL_PERMISSIONS: list[str] = [
    # Knowledge Base (Wiki)
    "kb.read",
    "kb.create",
    "kb.edit",
    "kb.delete",
    # Original Documents (Sources)
    "documents.read",
    "documents.create",
    "documents.edit",
    "documents.delete",
    # Departments
    "departments.read",
    "departments.create",
    "departments.edit",
    "departments.delete",
    # Employees
    "employees.read",
    "employees.create",
    "employees.edit",
    "employees.delete",
    # Roles (Position management)
    "roles.read",
    "roles.create",
    "roles.edit",
    "roles.delete",
    # Workspaces (Projects + Customers)
    "workspaces.read",
    "workspaces.create",
    "workspaces.edit",
    "workspaces.delete",
    # Settings
    "settings.read",
    "settings.edit",
    # Access Control
    "scopes.read",
    "scopes.manage",
    # Audit
    "audit.read",
]

PERMISSION_GROUPS: dict[str, list[str]] = {
    "Knowledge Base": ["kb.read", "kb.create", "kb.edit", "kb.delete"],
    "Documents":      ["documents.read", "documents.create", "documents.edit", "documents.delete"],
    "Departments":    ["departments.read", "departments.create", "departments.edit", "departments.delete"],
    "Employees":      ["employees.read", "employees.create", "employees.edit", "employees.delete"],
    "Roles":          ["roles.read", "roles.create", "roles.edit", "roles.delete"],
    "Workspaces":     ["workspaces.read", "workspaces.create", "workspaces.edit", "workspaces.delete"],
    "Settings":       ["settings.read", "settings.edit"],
    "Access Control": ["scopes.read", "scopes.manage", "audit.read"],
}

PERMISSION_LABELS: dict[str, str] = {
    # Knowledge Base
    "kb.read":             "View wiki pages",
    "kb.create":           "Create wiki pages",
    "kb.edit":             "Edit wiki pages",
    "kb.delete":           "Delete wiki pages",
    # Documents
    "documents.read":      "View & download documents",
    "documents.create":    "Upload documents",
    "documents.edit":      "Edit document metadata",
    "documents.delete":    "Delete documents",
    # Departments
    "departments.read":    "View departments",
    "departments.create":  "Create departments",
    "departments.edit":    "Edit departments",
    "departments.delete":  "Delete departments",
    # Employees
    "employees.read":      "View employees",
    "employees.create":    "Create employees",
    "employees.edit":      "Edit employees",
    "employees.delete":    "Deactivate employees",
    # Roles
    "roles.read":          "View roles & positions",
    "roles.create":        "Create roles",
    "roles.edit":          "Edit role permissions",
    "roles.delete":        "Delete roles",
    # Workspaces
    "workspaces.read":     "View workspaces",
    "workspaces.create":   "Create workspaces",
    "workspaces.edit":     "Edit workspaces & members",
    "workspaces.delete":   "Archive workspaces",
    # Settings
    "settings.read":       "View settings",
    "settings.edit":       "Change settings",
    # Access Control
    "scopes.read":         "View scope memberships",
    "scopes.manage":       "Manage scope memberships",
    "audit.read":          "View audit log",
}
