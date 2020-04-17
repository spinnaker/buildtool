package main

import (
	"container/list"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io/ioutil"
	"log"
	"os/exec"
	"regexp"
	"sort"
	"strings"

	"cloud.google.com/go/storage"
	"google.golang.org/api/option"
	"gopkg.in/yaml.v2"
)

var (
	bucket      = flag.String("bucket", "halconfig", "The GCS bucket name to read from. Must contain a bom/ directory.")
	jsonKeyPath = flag.String("jsonKey", "", "Filepath to JSON key with permission to read the --bucket")

	fromRepo = flag.String("from-project", "gcr.io/spinnaker-marketplace", "")
	toRepo   = flag.String("to-project", "us-docker.pkg.dev/spinnaker-community/releases", "")

	outPath = flag.String("out", "one-offs/migrate-containers.sh", "")

	releaseRegexp = regexp.MustCompile(`1\.[0-9]{1,2}\.[0-9]{1,2}\.yml`)
)

type Bom struct {
	Version      string       `yaml:"version"`
	Timestamp    string       `yaml:"timestamp"`
	Services     Services     `yaml:"services"`
	Dependencies Dependencies `yaml:"dependencies"`
}

type Services struct {
	Clouddriver      Service `yaml:"clouddriver"`
	Deck             Service `yaml:"deck"`
	Echo             Service `yaml:"echo"`
	Fiat             Service `yaml:"fiat"`
	Front50          Service `yaml:"front50"`
	Gate             Service `yaml:"gate"`
	Igor             Service `yaml:"igor"`
	Kayenta          Service `yaml:"kayenta"`
	MonitoringDaemon Service `yaml:"monitoring-daemon"`
	Orca             Service `yaml:"orca"`
	Rosco            Service `yaml:"rosco"`
}

func (s *Services) List() *list.List {
	l := list.New()
	l.PushBack(s.Clouddriver.WithName("clouddriver"))
	l.PushBack(s.Deck.WithName("deck"))
	l.PushBack(s.Echo.WithName("echo"))
	l.PushBack(s.Fiat.WithName("fiat"))
	l.PushBack(s.Front50.WithName("front50"))
	l.PushBack(s.Gate.WithName("gate"))
	l.PushBack(s.Igor.WithName("igor"))
	l.PushBack(s.Kayenta.WithName("kayenta"))
	l.PushBack(s.MonitoringDaemon.WithName("monitoring-daemon"))
	l.PushBack(s.Orca.WithName("orca"))
	l.PushBack(s.Rosco.WithName("rosco"))
	return l
}

type Dependencies struct {
	Consul Service `yaml:"consul"`
	Redis  Service `yaml:"redis"`
	Vault  Service `yaml:"vault"`
}

type Service struct {
	Commit  string `yaml:"commit,omitempty"`
	Version string `yaml:"version"`
	name    string
}

func (s *Service) WithName(n string) *Service {
	s.name = n
	return s
}

type Image struct {
	Digest string   `json:"digest"`
	Tags   []string `json:"tags"`
}

func main() {
	flag.Parse()

	ctx := context.Background()
	storageSvc, err := storage.NewClient(ctx, option.WithCredentialsFile(*jsonKeyPath), option.WithScopes(storage.ScopeReadOnly))
	if err != nil {
		log.Fatalf("Error generating new storage client: %v", err)
	}

	boms := make([]*Bom, 0, 100)
	iter := storageSvc.Bucket(*bucket).Objects(ctx, &storage.Query{Prefix: "bom/"})
	for obj, err := iter.Next(); err == nil; obj, err = iter.Next() {
		if releaseRegexp.Match([]byte(obj.Name)) {
			log.Printf("Matched %v\n", obj.Name)
			r, err := storageSvc.Bucket(*bucket).Object(obj.Name).NewReader(ctx)
			if err != nil {
				log.Fatalf("error reading object %v: %v", obj.Name, err)
			}
			bom := &Bom{}
			if err = yaml.NewDecoder(r).Decode(bom); err != nil {
				log.Fatalf("error decoding bom %v: %v", obj.Name, err)
			}
			boms = append(boms, bom)
		}
	}

	// Go get all the images and index them by tag
	imagesByTag := make(map[string]*Image, 1000)
	l := boms[0].Services.List()
	for svcElem := l.Front(); svcElem != nil; svcElem = svcElem.Next() {
		svc := svcElem.Value.(*Service)
		log.Printf("Executing gcloud for %v\n", svc.name)
		cmd := exec.Command("gcloud", "container", "images", "list-tags", fmt.Sprintf("%v/%v", *fromRepo, svc.name), "--format", "json")
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

		imageCount := 0
		tagCount := 0
		for _, img := range images {
			imageCount++
			for _, tag := range img.Tags {
				tagCount++
				fullTag := fmt.Sprintf("%v:%v", svc.name, tag)
				imagesByTag[fullTag] = img
			}
		}
		log.Printf("Indexed %v images and %v tags for %v service\n", imageCount, tagCount, svc.name)
	}

	cmds := make(map[string]bool)
	for _, bom := range boms {
		l := bom.Services.List()
		for svcElem := l.Front(); svcElem != nil; svcElem = svcElem.Next() {
			svc := svcElem.Value.(*Service)
			variantSuffixes := []string{
				"",
				"-slim",
				"-ubuntu",
				"-java8",
				"-ubuntu-java8",
			}

			for _, suffix := range variantSuffixes {
				fullTag := fmt.Sprintf("%v:%v%v", svc.name, svc.Version, suffix)
				img, ok := imagesByTag[fullTag]
				if !ok {
					log.Printf("Can't find %v for bom %v\n", fullTag, bom.Version)
					continue
				}

				b := strings.Builder{}
				b.WriteString(fmt.Sprintf("gcloud container images add-tag --quiet %v/%v ", *fromRepo, fullTag))
				if suffix == "" || suffix == "-slim" {
					// Only the Alpine-based variants get the top-level BOM version, since that's the preferred default.
					// This means for 1.16+, this puts the top-level BOM version on both the empty-suffix and "-slim"
					// variant, but this is a no-op because both tags point to the same digest anyway.
					//
					// Example:
					// gcloud container images add-tag gcr.io/spinnaker-marketplace/clouddriver:6.3.0-20190904130744		gcr.io/spinnaker-community/clouddriver:spinnaker-1.16.0	...	gcr.io/spinnaker-community/clouddriver:6.3.0-20190904130744-slim
					// gcloud container images add-tag gcr.io/spinnaker-marketplace/clouddriver:6.3.0-20190904130744-slim	gcr.io/spinnaker-community/clouddriver:spinnaker-1.16.0	...	gcr.io/spinnaker-community/clouddriver:6.3.0-20190904130744-slim
					//
					b.WriteString(fmt.Sprintf("%v/%v:spinnaker-%v ", *toRepo, svc.name, bom.Version))
				}

				for _, tag := range img.Tags {
					b.WriteString(fmt.Sprintf("%v/%v:%v ", *toRepo, svc.name, tag))
				}
				cmds[b.String()] = true
			}
		}
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
