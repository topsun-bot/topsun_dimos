# Sample Requirement for Pipeline Testing

Create a CLI tool `scripts/healthcheck.sh` that:
1. Checks if `claude` CLI is available
2. Checks if the `.pipeline/` directory exists
3. Reports the current git branch
4. Shows disk usage of the project directory
5. Outputs a brief summary of all checks

The script should:
- Exit with 0 if all checks pass
- Exit with 1 if any check fails
- Be well-commented and follow POSIX shell conventions
- Accept a `--verbose` flag for detailed output
