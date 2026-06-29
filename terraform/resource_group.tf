resource "aws_resourcegroups_group" "refund_group" {
  name = var.project_name

  resource_query {
    query = jsonencode({
      ResourceTypeFilters = ["AWS::AllSupported"]
      TagFilters = [{
        Key    = "Project"
        Values = [var.project_name]
      }]
    })
  }
}
