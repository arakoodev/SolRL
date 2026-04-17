run "artifact_bucket_plan" {
  command = plan

  assert {
    condition     = aws_s3_bucket.artifacts.bucket == "solrl-artifacts-local"
    error_message = "artifact bucket name changed"
  }
}

