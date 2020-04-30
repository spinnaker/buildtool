package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io/ioutil"
	"log"
	"os/exec"
	"regexp"
	"sort"
	"strings"
)

var (
	fromRepo = flag.String("from-project", "gcr.io/spinnaker-marketplace", "")
	toRepo   = flag.String("to-project", "us-docker.pkg.dev/spinnaker-community/releases", "")

	outPath = flag.String("out", "one-offs/migrate-halyard-containers/migrate-halyard-containers.sh", "")

	releaseRegexp = regexp.MustCompile(`^[01]\.[0-9]{1,2}\.[0-9]{1,2}(-slim|-ubuntu)?$`)
)

type Image struct {
	Digest     string   `json:"digest"`
	Tags       []string `json:"tags"`
	matchedTag string
}

func main() {
	flag.Parse()

	cmd := exec.Command("gcloud", "container", "images", "list-tags", fmt.Sprintf("%v/halyard", *fromRepo), "--format", "json")
	log.Printf("Executing: %v", cmd.String())
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		log.Fatalf("error getting gcloud stdout: %v", err)
	}
	if err := cmd.Start(); err != nil {
		log.Fatalf("error starting: %v", err)
	}

	images := make([]*Image, 0, 100)
	if err := json.NewDecoder(stdout).Decode(&images); err != nil {
		log.Fatalf("error decoding: %v", err)
	}
	if err := cmd.Wait(); err != nil {
		log.Fatalf("error waiting: %v", err)
	}

	keptImages := make([]*Image, 0, len(images))
	for _, img := range images {
		for _, tag := range img.Tags {
			if releaseRegexp.Match([]byte(tag)) {
				img.matchedTag = tag
				keptImages = append(keptImages, img)
				break
			}
		}
	}

	cmds := make(map[string]bool)
	for _, img := range keptImages {
		b := strings.Builder{}
		b.WriteString(fmt.Sprintf("gcloud container images add-tag --quiet %v/halyard:%v ", *fromRepo, img.matchedTag))
		for _, tag := range img.Tags {
			b.WriteString(fmt.Sprintf("%v/halyard:%v ", *toRepo, tag))
		}
		cmds[b.String()] = true
	}

	keys := make([]string, 0, len(cmds))
	for k, _ := range cmds {
		keys = append(keys, k)
	}
	sort.Strings(keys)

	if err := ioutil.WriteFile(*outPath, []byte(strings.Join(keys, "\n")), 0744); err != nil {
		log.Fatalf("error writing script: %v", err)
	}
}
