package main

import (
	"bufio"
	"context"
	"flag"
	"log"
	"regexp"
	"strings"

	"cloud.google.com/go/storage"
	"google.golang.org/api/iterator"
	"google.golang.org/api/option"
)

var (
	srcBucket   = flag.String("srcBucket", "halconfig", "The GCS bucket name to read from. Must contain a bom/ directory.")
	destBucket  = flag.String("destBucket", "halconfig2", "The GCS bucket name to write to.")
	jsonKeyPath = flag.String("jsonKey", "", "Filepath to JSON key with permission to read --srcBucket and write to the --destBucket")

	releaseRegexp = regexp.MustCompile(`1\.[0-9]{1,2}\.[0-9]{1,2}\.yml`)
)

func main() {
	flag.Parse()

	ctx := context.Background()
	var err error
	storageSvc, err := storage.NewClient(ctx, option.WithCredentialsFile(*jsonKeyPath), option.WithScopes(storage.ScopeFullControl))
	if err != nil {
		log.Fatalf("Error generating new storage client: %v", err)
	}

	iter := storageSvc.Bucket(*srcBucket).Objects(ctx, &storage.Query{Prefix: "bom/"})
	for obj, err := iter.Next(); err == nil; obj, err = iter.Next() {
		if releaseRegexp.MatchString(obj.Name) {
			log.Printf("Reading %v\n", obj.Name)
			r, err := storageSvc.Bucket(*srcBucket).Object(obj.Name).NewReader(ctx)
			if err != nil {
				log.Fatalf("error reading object %v: %v", obj.Name, err)
			}
			scanner := bufio.NewScanner(r)
			w := storageSvc.Bucket(*destBucket).Object(obj.Name).NewWriter(ctx)
			w.ObjectAttrs.ContentType = "application/x-yaml"
			for scanner.Scan() {
				t := scanner.Text()
				if strings.Contains(t, "gcr.io/spinnaker-marketplace") {
					t = strings.ReplaceAll(t, "gcr.io/spinnaker-marketplace", "us-docker.pkg.dev/spinnaker-community/releases")
					log.Printf("Replaced! New line: %v", t)
				}
				_, err := w.Write([]byte(t + "\n"))
				if err != nil {
					log.Fatalf("error writing output: %v", err)
				}
			}
			if scanner.Err() != nil {
				log.Fatalf("Error scanning: %v", scanner.Err())
			}
			err = w.Close()
			if err != nil {
				log.Fatalf("Error closing: %v", err)
			}
		}
	}

	if err != iterator.Done {
		log.Fatalf("Error iterating: %v", err)
	}
}
