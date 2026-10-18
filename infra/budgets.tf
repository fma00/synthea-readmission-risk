# Budgets notify; they do not cap spend (the Pub/Sub billing hard stop was declined in PRD OQ4). Amounts are in the
# billing account's currency, CHF. Neither budget sets all_updates_rule, so notifications are the default emails to the
# billing account's admins and users.

# The CHF 20 budget was created with gcloud on 2026-10-09 (docs/setup.md). This block stays after the first apply: a
# no-op while the budget is in state, and the record that it pre-dates Terraform. Teardown step 1 deletes it together
# with the resource block below before `state rm`, so the budget is never re-imported.
import {
  to = google_billing_budget.monthly
  id = "billingAccounts/${var.billing_account}/budgets/${var.existing_budget_id}"
}

resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account
  display_name    = "synthea-readmission-risk monthly"

  budget_filter {
    projects               = ["projects/${var.project_number}"]
    calendar_period        = "MONTH"
    credit_types_treatment = "INCLUDE_ALL_CREDITS"
  }

  amount {
    specified_amount {
      currency_code = "CHF"
      units         = "20"
    }
  }

  threshold_rules {
    threshold_percent = 0.5
    spend_basis       = "CURRENT_SPEND"
  }
  threshold_rules {
    threshold_percent = 0.9
    spend_basis       = "CURRENT_SPEND"
  }
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "CURRENT_SPEND"
  }
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  # The standing guard: it outlives a teardown of everything else (docs/setup.md > Teardown checklist).
  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.enabled]
}

# PRD OQ4: an early warning next to the CHF 20 budget.
resource "google_billing_budget" "early_warning" {
  billing_account = var.billing_account
  display_name    = "synthea-readmission-risk early warning"

  budget_filter {
    projects               = ["projects/${var.project_number}"]
    calendar_period        = "MONTH"
    credit_types_treatment = "INCLUDE_ALL_CREDITS"
  }

  amount {
    specified_amount {
      currency_code = "CHF"
      units         = "5"
    }
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "CURRENT_SPEND"
  }
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  depends_on = [google_project_service.enabled]
}
