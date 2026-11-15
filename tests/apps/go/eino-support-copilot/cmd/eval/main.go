package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"strings"

	"github.com/guiyangyuan/eino-support-copilot/internal/evaluation"
)

func main() {
	os.Exit(run())
}

func run() int {
	flags := flag.NewFlagSet("eval", flag.ContinueOnError)
	datasetPath := flags.String("dataset", "seed/eval/support-golden.jsonl", "path to a JSONL golden dataset")
	mode := flags.String("mode", "mock", "evaluation mode")
	outputPath := flags.String("output", "", "optional path for the formatted JSON result")
	if err := flags.Parse(os.Args[1:]); err != nil {
		return 2
	}
	if *mode != "mock" {
		fmt.Fprintln(os.Stderr, "only deterministic mock evaluation is available from this CLI build")
		return 2
	}
	dataset, err := evaluation.LoadJSONL(*datasetPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		if errors.Is(err, evaluation.ErrInvalidDataset) || strings.Contains(err.Error(), "dataset") {
			return 2
		}
		return 1
	}
	commitSHA := strings.TrimSpace(os.Getenv("GIT_COMMIT_SHA"))
	if commitSHA == "" {
		commitSHA = "local"
	}
	result, err := evaluation.NewRunner(evaluation.ExpectedExecutor{}, evaluation.RunConfig{CommitSHA: commitSHA, Mode: *mode}).Run(context.Background(), dataset)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(result); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	if strings.TrimSpace(*outputPath) != "" {
		if err := writeResult(*outputPath, result); err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 1
		}
	}
	for _, item := range result.Results {
		if !item.Passed {
			return 1
		}
	}
	return 0
}

func writeResult(path string, result evaluation.RunResult) error {
	data, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return fmt.Errorf("encode evaluation result: %w", err)
	}
	data = append(data, '\n')
	if err := os.WriteFile(path, data, 0o644); err != nil {
		return fmt.Errorf("write evaluation result: %w", err)
	}
	return nil
}
